"""LFCController: one object owning every instrument, keyword bindings,
and the comb state.

Replaces the monolithic ``KeckLFC`` class. Differences from the old design:

* instruments are built once from config and stay connected (no
  connect/act/disconnect per keyword);
* keyword access goes through :class:`~keckogeco.comb.keywords.KeywordRegistry`
  with schema validation, instead of same-named methods;
* bindings are registered per configured device, so a rack with a device
  disabled in config simply reports those keywords as unimplemented.

Phase-1 scope: tier-1 keyword bindings (amplifiers, RF supplies, seed
laser, interlock, TECs, DAQ temperatures, IM bias, comb-state checks).
Monitors, transition sequences, and autolocks land in Phase 2.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import ClassVar

from ..check import build_device
from ..config import Config
from ..drivers.base import Instrument
from ..drivers.errors import InstrumentError
from . import state as state_mod
from .actions import ActionExecutor
from .keywords import KeywordRegistry
from .monitors import Heartbeat, TelemetryLogger
from .state import CombState, SubsystemStatus

__all__ = ["LFCController"]

log = logging.getLogger(__name__)


class LFCController:
    """Owns the instruments and answers keyword reads/writes.

    Parameters
    ----------
    config : Config
    sim : bool
        Build every device with a simulated transport.
    """

    def __init__(self, config: Config, sim: bool = False):
        self.config = config
        self.sim = sim
        self.devices: dict[str, Instrument] = {}
        self.offline: dict[str, str] = {}  # device key -> last error
        self.registry = KeywordRegistry()
        self.executor = ActionExecutor(self)
        self.monitors: list = []
        # in-memory backing for ICE/TEST keywords (no hardware behind them)
        self._softstore: dict[str, object] = {"ICESTA": 1, "ICETEST": False}
        # (bias_V, input_V) points streamed by the im_bias_scan action;
        # served as the im_scan array so the GUI can plot the sweep live
        self.im_scan_points: list[tuple[float, float]] = []
        self._started = False

    # ------------------------------------------------------------ lifecycle

    def start(self) -> None:
        """Build and connect every enabled device; tolerate failures
        (device marked offline), then register keyword bindings."""
        for key, dev_cfg in self.config.enabled_devices().items():
            try:
                device = build_device(dev_cfg, sim=self.sim)
                device.connect()
                self.devices[key] = device
            except Exception as exc:  # noqa: BLE001 - startup must not die on one device
                self.offline[key] = str(exc)
                log.error("device %s offline at startup: %s", key, exc)
        self._register_keywords()
        self._register_arrays()
        self._start_monitors()
        self._started = True
        log.info(
            "controller started: %d device(s) online, %d offline%s",
            len(self.devices),
            len(self.offline),
            " [SIM]" if self.sim else "",
        )

    def _start_monitors(self) -> None:
        self.heartbeat = Heartbeat(self.registry)
        self.monitors.append(self.heartbeat)
        if self.config.logging.telemetry_s > 0:
            telemetry_dir = Path(self.config.logging.dir) / "telemetry"
            self.monitors.append(
                TelemetryLogger(self.registry, telemetry_dir, self.config.logging.telemetry_s)
            )
        for monitor in self.monitors:
            monitor.start()

    def stop(self) -> None:
        for monitor in self.monitors:
            monitor.stop()
        self.monitors.clear()
        self.executor.abort()
        self.executor.join(timeout=5)
        for key, device in self.devices.items():
            try:
                device.close()
            except InstrumentError as exc:
                log.warning("closing %s: %s", key, exc)
        self.devices.clear()
        self._started = False

    def __enter__(self) -> LFCController:
        self.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self.stop()

    def device(self, key: str) -> Instrument:
        device = self.devices.get(key)
        if device is None:
            reason = self.offline.get(key, "not in config or disabled")
            raise InstrumentError(f"device {key!r} unavailable: {reason}")
        return device

    def psu_channel(self, key: str) -> int:
        """Which supply channel a subsystem uses (``channel`` config option).

        The RF oscillator runs on GPD channel 2 per the commissioned
        minicomb setup; anything unspecified defaults to channel 1.
        """
        dev_cfg = self.config.devices.get(key)
        if dev_cfg is None:
            return 1
        return int(dev_cfg.options.get("channel", 1))

    # ------------------------------------------------------------- keywords

    def read(self, name: str):
        return self.registry.read(name)

    def write(self, name: str, raw):
        return self.registry.write(name, raw)

    def _register_keywords(self) -> None:
        """Bind tier-1 keywords for whichever devices are configured."""
        bind = self.registry.bind
        has = self.config.enabled_devices().__contains__

        # --- Amonics EDFAs: power setpoint + on/off + input monitors
        for key, kw in (
            ("edfa27", "LFC_EDFA27"),
            ("edfa13", "LFC_EDFA13"),
            ("edfa23", "LFC_EDFA23"),
        ):
            if not has(key):
                continue

            def edfa(k=key):
                return self.device(k)

            bind(
                f"{kw}_P",
                getter=lambda k=key: self.device(k).setpoint(),
                setter=lambda v, k=key: self.device(k).set_setpoint(v),
            )
            bind(
                f"{kw}_ONOFF",
                getter=lambda k=key: self.device(k).activation,
                setter=lambda v, k=key: self._edfa_onoff(k, v),
            )
            bind(
                f"{kw}_INPUT_POWER_MONITOR",
                getter=lambda k=key: self.device(k).input_power_mW(),
            )
            bind(
                f"{kw}_OUTPUT_POWER_MONITOR",
                getter=lambda k=key: self.device(k).output_power_mW(),
            )

        # --- Pritel amplifier + Arduino interlock
        # Keyword units follow the deployed KTL semantics: PRE_P in mA,
        # PTAMP_I in A (old code gated at 4.2 A), PTAMP_OUT in W.
        if has("ptamp"):
            bind(
                "LFC_PTAMP_PRE_P",
                getter=lambda: self.device("ptamp").preamp_mA,
                setter=lambda v: self.device("ptamp").set_preamp_mA(v),
            )
            bind(
                "LFC_PTAMP_I",
                getter=lambda: self.device("ptamp").pwramp_mA / 1000,
                setter=lambda v: self.device("ptamp").set_pwramp_mA(v * 1000),
            )
            bind("LFC_PTAMP_OUT", getter=lambda: self.device("ptamp").output_power_mW / 1000)
            bind("LFC_PTAMP_IN", getter=lambda: self.device("ptamp").input_power_mW)
            bind(
                "LFC_PTAMP_ONOFF",
                getter=lambda: self.device("ptamp").pump_on,
                setter=lambda v: self.device("ptamp").set_pump(v),
            )
        if has("arduino_relay"):
            bind(
                "LFC_PTAMP_LATCH",
                getter=self._latch_state,
                setter=lambda _v: self.device("arduino_relay").reset_latch(),
            )
            # the interlock's photodiode voltage: raw 10-bit ADC counts
            # over a 0-5 V range, reported in volts
            bind(
                "LFC_PTAMP_INTERLOCK_V",
                getter=lambda: (
                    self.device("arduino_relay").relay_status().voltage_now * 5.0 / 1023.0
                ),
            )
            bind(
                "LFC_YJ_SHUTTER",
                getter=lambda: self.device("arduino_relay").yj_open,
                setter=self._set_yj,
            )

        # --- RIO seed laser
        if has("rio"):
            bind(
                "LFC_RIO_T",
                getter=lambda: self.device("rio").tec_setpoint_C(volatile=True),
                setter=lambda v: self.device("rio").set_tec_setpoint_C(v),
            )
            bind(
                "LFC_RIO_I",
                getter=lambda: self.device("rio").diode_current_mA(volatile=True),
                setter=lambda v: self.device("rio").set_diode_current_mA(v),
            )

        # --- RF chain power supplies (channels from the `channel` config
        # option: oscillator = GPD CH2 per the commissioned minicomb setup)
        for dev_key, kw in (("rf_osc_psu", "LFC_RFOSCI"), ("rf_amp_psu", "LFC_RFAMP")):
            if not has(dev_key):
                continue
            channel = self.psu_channel(dev_key)
            bind(
                f"{kw}_ONOFF",
                getter=lambda d=dev_key, c=channel: self.device(d).output_on(c),
                setter=lambda v, d=dev_key, c=channel: self.device(d).set_output(v, c),
            )
            bind(
                f"{kw}_I",
                getter=lambda d=dev_key, c=channel: self.device(d).output_current_A(c),
            )
            bind(
                f"{kw}_V",
                getter=lambda d=dev_key, c=channel: self.device(d).output_voltage_V(c),
            )

        # --- TECs (setpoint changes stepped in 0.5 C increments, as the old
        # LFC_PPLN_T/LFC_WGD_T did to avoid thermal shocks to the crystals)
        if has("tec_ppln"):
            bind(
                "LFC_PPLN_T",
                getter=lambda: self.device("tec_ppln").temperature_C,
                setter=lambda v: self._ramp_tec("tec_ppln", v),
            )
        if has("tec_wvg"):
            bind(
                "LFC_WGD_T",
                getter=lambda: self.device("tec_wvg").temperature_C,
                setter=lambda v: self._ramp_tec("tec_wvg", v),
            )

        # --- IM bias via the SIM960 servo (slot from config option im_slot).
        # Slot 3 is the old system's "Minicomb Intensity Lock Servo"
        # (KeckLFC.py __LFC_IM_LOCK_connect); slot 5 is the Rb lock servo.
        if has("srs"):
            im_slot = int(self.config.devices["srs"].options.get("im_slot", 3))
            self._im_servo = self.device("srs").sim960(im_slot, "IM bias servo")
            bind(
                "LFC_IM_BIAS",
                getter=lambda: self._im_servo.manual_output_V,
                setter=lambda v: setattr(self._im_servo, "manual_output_V", v),
            )

        # --- Rack thermocouples (DAQ board 0; channel map verified 2023-06)
        if has("daq"):
            daq_map = {
                "LFC_T_RACK_MID": 0,  # rack side baffle
                "LFC_T_RACK_TOP": 1,  # waveshaper, upper rack
                "LFC_T_GLY_RACK_OUT": 4,
                "LFC_T_GLY_RACK_IN": 5,
                "LFC_T_RACK_BOT": 6,  # power supply shelf
            }
            for kw, channel in daq_map.items():
                bind(kw, getter=lambda ch=channel: self.device("daq").temperature_C(ch))
        if has("daq_eocb"):
            bind("LFC_T_EOCB_OUT", getter=lambda: self.device("daq_eocb").temperature_C(4))
            bind("LFC_T_EOCB_IN", getter=lambda: self.device("daq_eocb").temperature_C(5))

        # --- VOAs, HK shutter, pendulum rep-rate monitor
        # Which physical VOA is which wavelength is not yet known; config
        # keys stay unit-serial-based (voa_303699, ...) until each unit is
        # identified on-site, so these keywords stay unbound until a config
        # block is renamed to the matching wavelength key below.
        for key, kw in (
            ("voa1550", "LFC_VOA1550_ATTEN"),
            ("voa1310", "LFC_VOA1310_ATTEN"),
            ("voa2000", "LFC_VOA2000_ATTEN"),
        ):
            if has(key):
                bind(
                    kw,
                    getter=lambda k=key: self.device(k).attenuation_dB,
                    setter=lambda v, k=key: setattr(self.device(k), "attenuation_dB", v),
                )
        if has("hk_shutter"):
            bind(
                "LFC_HK_SHUTTER",
                getter=lambda: self.device("hk_shutter").open,
                setter=lambda v: self.device("hk_shutter").set_open(v),
            )
        if has("pendulum"):
            bind("LFC_PENDULEM_FREQ_MONITOR", getter=lambda: self._rep_rate_ok() is True)

        # --- Agiltron 2x2 switch (enumerated: 1 = YJ path, 2 = HK path)
        if has("switch2x2"):
            bind(
                "LFC_2BY2_SWITCH",
                getter=lambda: self.device("switch2x2").position,
                setter=lambda v: self.device("switch2x2").set_position(v),
            )

        # --- Clarity laser: the keyword collapses the 4-state status to
        # off/on exactly like the old orchestration (1/2/3 -> 1)
        if has("clarity"):
            bind(
                "LFC_CLARITY_ONOFF",
                getter=lambda: 1 if self.device("clarity").status_code > 0 else 0,
                setter=lambda v: self.device("clarity").set_output(bool(int(v))),
            )

        # --- IM bias auto-lock (write 1 to run; enqueued like transitions)
        if has("srs"):
            bind(
                "LFC_IM_AUTO_LOCK",
                getter=lambda: self._action_result("im_auto_lock"),
                setter=lambda v: self._submit_if_true("im_auto_lock", v),
            )

        # --- WaveShaper scalar keywords: WSP_PHASE programs 2nd-order
        # dispersion (GDD, d2 in ps/nm) and WSP_TOD 3rd-order (d3 in
        # ps/nm^2) — the two are applied together as one phase profile;
        # WSP_ATTEN a flat attenuation in dB. Reads report the value
        # currently applied (softstore).
        if has("waveshaper1"):
            bind(
                "LFC_WSP_PHASE",
                getter=lambda: self._softstore.get("LFC_WSP_PHASE", 0.0),
                setter=lambda v: self._set_wsp_dispersion(d2_ps_nm=v),
            )
            bind(
                "LFC_WSP_TOD",
                getter=lambda: self._softstore.get("LFC_WSP_TOD", 0.0),
                setter=lambda v: self._set_wsp_dispersion(d3_ps_nm2=v),
            )
            bind(
                "LFC_WSP_CENTER",
                getter=lambda: self._softstore.get("LFC_WSP_CENTER", 1559.8),
                setter=lambda v: self._set_wsp_dispersion(center_nm=v),
            )
            bind(
                "LFC_WSP_ATTEN",
                getter=lambda: self._softstore.get("LFC_WSP_ATTEN", 0.0),
                setter=self._set_wsp_atten,
            )

        # --- comb state
        bind("LFC_CHECK_STATUS", getter=lambda: state_mod.legacy_code(self.subsystem_status()))
        bind(
            "LFC_CHECK_FULLCOMB",
            getter=lambda: 1 if self.comb_state() == CombState.FULL_COMB else 0,
        )

        # --- transitions: KTL writes enqueue the action (non-blocking);
        # progress is visible via LFC_CHECK_STATUS and /actions/current
        for kw, action in (
            ("LFC_SET_STANDBY", "set_standby"),
            ("LFC_SET_FULL_COMB", "set_full_comb"),
            ("LFC_SET_OFF", "set_off"),
            ("LFC_MINICOMB_AUTO_SETUP", "minicomb_auto_setup"),
            ("LFC_CLOSE_ALL", "close_all"),
        ):
            bind(
                kw,
                getter=lambda a=action: self._action_result(a),
                setter=lambda v, a=action: self._submit_if_true(a, v),
            )

        # --- IM lock mode + the mini-comb RF VCA (RF osc supply channel 3)
        if has("srs"):
            bind(
                "LFC_IM_LOCK_MODE",
                getter=lambda: self._im_servo.output_mode == "PID",
                setter=lambda v: setattr(self._im_servo, "output_mode", "PID" if v else "MAN"),
            )
        if has("rf_osc_psu"):
            bind(
                "LFC_IM_RF_ATT",
                getter=lambda: self.device("rf_osc_psu").output_voltage_V(3),
                setter=lambda v: self.device("rf_osc_psu").set_voltage_V(v, 3),
            )

        # --- commissioned preset keywords: write 1 to push the default
        # setpoints (values match the transition sequences); reads are False
        presets: dict[str, object] = {}
        if has("edfa27"):
            presets["LFC_EDFA27_P_DEFAULT"] = lambda: self._edfa_default("edfa27", "APC", 450)
            presets["LFC_EDFA27_AUTO_ON"] = lambda: self._edfa_default(
                "edfa27", "APC", 450, turn_on=True
            )
        # EDFA23 default is 0 mA (not the commissioned 80) while the 23 dB
        # EDFA is out of service — parked dark even when activated
        if has("edfa23"):
            presets["LFC_EDFA23_P_DEFAULT"] = lambda: self._edfa_default("edfa23", "ACC", 0)
            presets["LFC_EDFA23_AUTO_ON"] = lambda: self._edfa_default(
                "edfa23", "ACC", 0, turn_on=True
            )
        if has("rf_amp_psu"):
            presets["LFC_RFAMP_DEFAULT"] = lambda: self._psu_default("rf_amp_psu", 30, 4.2)
        if has("rf_osc_psu"):
            presets["LFC_RFOSCI_DEFAULT"] = lambda: self._psu_default("rf_osc_psu", 15, 3)
        if has("ptamp"):
            presets["LFC_PTAMP_PRE_P_DEFAULT"] = lambda: self.device("ptamp").set_preamp_mA(600)
            presets["LFC_PTAMP_I_DEFAULT"] = lambda: self.device("ptamp").set_pwramp_mA(3900)
        for kw, apply in presets.items():
            bind(
                kw,
                getter=lambda: False,
                setter=lambda v, a=apply: a() if v else None,
            )

        # --- range monitors (True = OK). Out-of-range logs an error; the
        # old auto-CLOSE_ALL/email reaction is deliberately gone (see
        # ktl/keyword-changes.md, same policy as the rep-rate monitor).
        if has("daq"):
            bind("LFC_TEMP_MONITOR", getter=self._temps_ok)
            bind(
                "LFC_TEMP_TEST1",
                getter=lambda: [self.device("daq").temperature_C(ch) for ch in range(8)],
            )
        if has("daq_eocb"):
            bind(
                "LFC_TEMP_TEST2",
                getter=lambda: [self.device("daq_eocb").temperature_C(ch) for ch in range(8)],
            )
        if has("rf_osc_psu"):
            bind("LFC_RFOSCI_MONITOR", getter=self._rfosc_ok)
        if has("rf_amp_psu"):
            bind("LFC_RFAMP_MONITOR", getter=self._rfamp_ok)

        # --- legacy near-no-ops kept for KTL compatibility: LFC_YJ_SHUT is
        # proposed for retirement (the old handler was already a stub) and
        # SHOW_ALL_VAL dumps the snapshot to the log instead of stdout
        bind("LFC_YJ_SHUT", getter=lambda: 0, setter=self._yj_shut)
        bind("SHOW_ALL_VAL", getter=lambda: False, setter=self._show_all_val)

        # --- ICE-transport keywords (semantics preserved for the KTL side)
        bind("ICECLK", getter=lambda: int(time.time()))
        bind(
            "ICECLK_ONOFF",
            getter=lambda: self.heartbeat.enabled if self.monitors else False,
            setter=self._set_heartbeat,
        )
        bind(
            "ICESTA",
            getter=lambda: self._softstore["ICESTA"],
            setter=lambda v: self._softstore.__setitem__("ICESTA", v),
        )
        bind("ICESTA2", getter=lambda: 1)
        bind(
            "ICETEST",
            getter=lambda: self._softstore["ICETEST"],
            setter=lambda v: self._softstore.__setitem__("ICETEST", v),
        )

        # --- TEST* keywords: pure soft values for dispatcher integration
        for name, default in (
            ("TESTMODE", False),
            ("TESTINT", 0),
            ("TESTFLOAT", 0.0),
            ("TESTENUM", 0),
            ("TESTSTRING", ""),
            ("TESTARRAY", [0.0, 0.0]),
        ):
            self._softstore.setdefault(name, default)
            spec = self.registry.schema.get(name)
            if spec is None:
                continue
            bind(
                name,
                getter=lambda n=name: self._softstore[n],
                setter=(lambda v, n=name: self._softstore.__setitem__(n, v))
                if spec.writable
                else None,
            )

        log.info(
            "keyword bindings: %d bound, %d unbound",
            len(self.registry.bound),
            len(self.registry.missing_getters()),
        )

    def _register_arrays(self) -> None:
        """Array data sources for /api/v1/arrays (spectra, WS profiles)."""
        self.arrays: dict = {}
        if "osa" in self.devices:

            def osa_spectrum() -> dict:
                wavelength, power = self.device("osa").get_spectrum()
                return {
                    "x": wavelength.tolist(),
                    "y": power.tolist(),
                    "x_label": "wavelength (nm)",
                    "y_label": "power (dBm)",
                }

            self.arrays["osa_spectrum"] = osa_spectrum
        if "waveshaper1" in self.devices:

            def wsp_profile() -> dict:
                freq, atten, phase = self.device("waveshaper1").profile_arrays()
                return {
                    "x": freq.tolist(),
                    "y": (-atten).tolist(),
                    "y2": phase.tolist(),
                    "x_label": "frequency (THz)",
                    "y_label": "-attenuation (dB)",
                    "y2_label": "phase (rad)",
                }

            self.arrays["wsp_profile"] = wsp_profile
        if getattr(self, "_im_servo", None) is not None:

            def im_scan() -> dict:
                # list() snapshots the append-only point list (the scan
                # action's thread may be mid-sweep)
                points = list(self.im_scan_points)
                current = self.executor.current() or {}
                running = bool(current.get("running")) and current.get("name") == "im_bias_scan"
                payload = {
                    "x": [bias for bias, _ in points],
                    "y": [value for _, value in points],
                    "x_label": "IM bias (V)",
                    "y_label": "photodiode (V)",
                    "running": running,
                }
                if not running:
                    # live servo readouts for the GUI panel (strip charts +
                    # lock controls); skipped during a scan so the poller
                    # doesn't compete for the mainframe. bias_V is OMON —
                    # the voltage actually at the output, which the PID
                    # moves around while locked (MOUT is only the manual
                    # setting).
                    try:
                        payload["bias_V"] = self._im_servo.output_V
                        payload["input_V"] = self._im_servo.measure_input_V
                        payload["mode"] = self._im_servo.output_mode
                        payload["setpoint_V"] = self._im_servo.setpoint_V
                    except InstrumentError as exc:
                        log.debug("im_scan live readout failed: %s", exc)
                return payload

            self.arrays["im_scan"] = im_scan

    # ------------------------------------------- presets and range monitors

    #: rack thermocouple alarm level for LFC_TEMP_MONITOR
    RACK_TEMP_MAX_C: ClassVar[float] = 40.0

    def _edfa_default(self, key: str, mode: str, setpoint: float, turn_on: bool = False) -> None:
        """Push the commissioned EDFA setpoint (and optionally emit)."""
        edfa = self.device(key)
        edfa.set_mode(mode)
        edfa.set_setpoint(setpoint)
        if turn_on:
            edfa.set_channel(True)
            edfa.activate()
        log.info("%s preset applied: %s %s%s", key, mode, setpoint, " + ON" if turn_on else "")

    def _psu_default(self, key: str, volts: float, amps: float) -> None:
        """Push the commissioned supply limits (does not switch the output)."""
        psu = self.device(key)
        channel = self.psu_channel(key)
        psu.set_voltage_V(volts, channel)
        psu.set_current_A(amps, channel)
        log.info("%s preset applied: %.1f V / %.2f A on channel %d", key, volts, amps, channel)

    def _temps_ok(self) -> bool:
        daq = self.device("daq")
        hot = {
            daq.positions[ch]: t
            for ch in range(8)
            if (t := daq.temperature_C(ch)) > self.RACK_TEMP_MAX_C
        }
        if hot:
            log.error("rack temperature(s) above %.0f C: %s", self.RACK_TEMP_MAX_C, hot)
        return not hot

    def _rfosc_ok(self) -> bool:
        """RF oscillator supply within the commissioned envelope (or off)."""
        psu = self.device("rf_osc_psu")
        channel = self.psu_channel("rf_osc_psu")
        if not psu.output_on(channel):
            return True
        volts = psu.output_voltage_V(channel)
        amps = psu.output_current_A(channel)
        ok = abs(volts - 15) <= 1 and abs(amps - 0.4) <= 0.1
        if not ok:
            log.error(
                "RF oscillator out of range: %.2f V / %.3f A (expect 15 V, ~0.4 A draw)",
                volts,
                amps,
            )
        return ok

    def _rfamp_ok(self) -> bool:
        """RF amplifier supply within the commissioned envelope (or off).

        The expected current draw depends on whether the oscillator is
        driving it: ~4.2 A seeded, ~0.7 A idling (old monitor's numbers).
        Seeded tolerance is ±0.5 A: the rack unit draws 3.87 A (measured
        2026-07-12), comfortably healthy but outside the old ±0.15 A.
        """
        psu = self.device("rf_amp_psu")
        channel = self.psu_channel("rf_amp_psu")
        if not psu.output_on(channel):
            return True
        volts = psu.output_voltage_V(channel)
        amps = psu.output_current_A(channel)
        osc_on = False
        if "rf_osc_psu" in self.devices:
            osc_on = self.device("rf_osc_psu").output_on(self.psu_channel("rf_osc_psu"))
        expect_amps, tol_amps = (4.2, 0.5) if osc_on else (0.7, 0.15)
        ok = abs(volts - 30) <= 1 and abs(amps - expect_amps) <= tol_amps
        if not ok:
            log.error(
                "RF amplifier out of range: %.2f V / %.3f A (expect 30 V, ~%.1f A draw)",
                volts,
                amps,
                expect_amps,
            )
        return ok

    def _yj_shut(self, value) -> None:
        log.info(
            "LFC_YJ_SHUT write (%s) ignored: keyword proposed for retirement, "
            "see ktl/keyword-changes.md",
            value,
        )

    def _show_all_val(self, value) -> None:
        if not value:
            return
        for name, reading in sorted(self.registry.snapshot().items()):
            log.info("SHOW_ALL_VAL: %s = %s", name, reading)

    # ------------------------------------------------------------- helpers

    def _edfa_onoff(self, key: str, on: bool) -> None:
        device = self.device(key)
        if on:
            device.set_channel(True)
            device.activate()
        else:
            device.deactivate()
            device.set_channel(False)

    def _set_yj(self, open_shutter: bool) -> None:
        relay = self.device("arduino_relay")
        if open_shutter:
            relay.open_yj()
        else:
            relay.close_yj()

    def _ramp_tec(self, key: str, target_C: float) -> None:
        """Step a TC-720 setpoint in 0.5 C increments with settle waits,
        as the old LFC_PPLN_T/LFC_WGD_T setters did."""
        tec = self.device(key)
        now_C = tec.temperature_C
        step = 0.5 if target_C >= now_C else -0.5
        value = now_C
        while abs(target_C - value) > 0.5:
            value += step
            tec.set_temperature_C(round(value, 2))
            if not self.sim:
                time.sleep(4)
        tec.set_temperature_C(target_C)

    def _submit_if_true(self, action: str, value) -> None:
        """Transition keywords fire on a truthy write (modify kw=1)."""
        if value in (1, True):
            self.executor.submit(action)

    def _action_result(self, action: str) -> int:
        current = self.executor.current()
        if current and current["name"] == action and current["error"] is None:
            return 1
        return 0

    def _set_heartbeat(self, on: bool) -> None:
        if self.monitors:
            self.heartbeat.enabled = bool(on)

    def _set_wsp_dispersion(
        self,
        d2_ps_nm: float | None = None,
        d3_ps_nm2: float | None = None,
        center_nm: float | None = None,
    ) -> None:
        """Program GDD + TOD around the center wavelength as one phase
        profile; each argument updates its stored value and the others
        keep their last applied one. The center defaults to the
        commissioned 1559.8 nm (old orchestration: d2=2.14, d3=0)."""
        if d2_ps_nm is not None:
            self._softstore["LFC_WSP_PHASE"] = float(d2_ps_nm)
        if d3_ps_nm2 is not None:
            self._softstore["LFC_WSP_TOD"] = float(d3_ps_nm2)
        if center_nm is not None:
            self._softstore["LFC_WSP_CENTER"] = float(center_nm)
        ws = self.device("waveshaper1")
        ws.set_dispersion(
            d2_ps_nm=self._softstore.get("LFC_WSP_PHASE", 0.0),
            d3_ps_nm2=self._softstore.get("LFC_WSP_TOD", 0.0),
            center_nm=self._softstore.get("LFC_WSP_CENTER", 1559.8),
        )
        ws.write_profile()

    def _set_wsp_atten(self, atten_dB: float) -> None:
        ws = self.device("waveshaper1")
        level = float(atten_dB)
        ws.atten = lambda f: level
        ws.atten_description = f"flat {level:.1f} dB"
        ws.write_profile()
        self._softstore["LFC_WSP_ATTEN"] = level

    def _latch_state(self) -> int:
        """LFC_PTAMP_LATCH enum: 1 ready, 0 stop-but-resettable,
        3 too-high, 5 too-low, 4 unknown."""
        relay = self.device("arduino_relay").relay_status()
        if relay.ok_to_amplify:
            return 1
        if relay.resettable:
            return 0
        if relay.voltage_now >= relay.high_threshold:
            return 3
        if relay.voltage_now <= relay.low_threshold:
            return 5
        return 4

    # ----------------------------------------------------------- comb state

    #: rep rate must be within this of 16 GHz to count as detected
    REP_RATE_HZ = 16e9
    REP_RATE_TOLERANCE_HZ = 1000.0

    def _rep_rate_ok(self) -> bool:
        """Rep-rate factor of the comb state (Pendulum counter, channel C).

        The old check only consulted the counter when both RF supplies were
        on — and it shut the whole comb down (CLOSE_ALL + email) on a bad
        reading. Auto-shutdown from inside a status probe is a hair
        trigger, so here a bad rep rate is reported (and logged loudly) but
        acting on it is left to the operator / a future safety monitor.
        """
        rf_on = self.device("rf_osc_psu").output_on(self.psu_channel("rf_osc_psu")) and self.device(
            "rf_amp_psu"
        ).output_on(self.psu_channel("rf_amp_psu"))
        if not rf_on:
            return False
        if "pendulum" not in self.devices:
            return True  # counter unavailable: fall back to the RF-chain inference
        frequency = self.device("pendulum").measure_frequency_Hz("c")
        ok = abs(frequency - self.REP_RATE_HZ) <= self.REP_RATE_TOLERANCE_HZ
        if not ok:
            log.error(
                "rep rate %.6f GHz is off 16 GHz by %+.0f Hz - CHECK THE RF CHAIN "
                "(the old system would have shut the comb down here)",
                frequency / 1e9,
                frequency - self.REP_RATE_HZ,
            )
        return ok

    def subsystem_status(self) -> SubsystemStatus:
        """Probe the six state-defining subsystems (offline -> None)."""

        def probe(func):
            try:
                return bool(func())
            except Exception:  # noqa: BLE001 - offline/unreadable -> unknown
                return None

        return SubsystemStatus(
            ptamp_on=probe(lambda: self.device("ptamp").pump_on),
            edfa23_on=probe(lambda: self.device("edfa23").activation),
            edfa27_on=probe(lambda: self.device("edfa27").activation),
            rf_oscillator_on=probe(
                lambda: self.device("rf_osc_psu").output_on(self.psu_channel("rf_osc_psu"))
            ),
            rf_amplifier_on=probe(
                lambda: self.device("rf_amp_psu").output_on(self.psu_channel("rf_amp_psu"))
            ),
            rep_rate_detected=probe(self._rep_rate_ok),
        )

    def comb_state(self) -> CombState:
        return state_mod.evaluate(self.subsystem_status())

    def state_summary(self) -> dict:
        status = self.subsystem_status()
        return {
            "state": state_mod.evaluate(status).value,
            "legacy_code": state_mod.legacy_code(status),
            "subsystems": {
                "ptamp": status.ptamp_on,
                "edfa23": status.edfa23_on,
                "edfa27": status.edfa27_on,
                "rf_oscillator": status.rf_oscillator_on,
                "rf_amplifier": status.rf_amplifier_on,
                "rep_rate": status.rep_rate_detected,
            },
            "devices_online": sorted(self.devices),
            "devices_offline": dict(self.offline),
            "action": self.executor.current(),
            "sim": self.sim,
        }
