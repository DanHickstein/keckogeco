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
            monitor = f"{kw}_INPUT_POWER_MONITOR"
            if monitor in self.registry.schema:
                bind(monitor, getter=lambda k=key: self.device(k).input_power_mW())

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

        # --- IM bias via the SIM960 servo (slot from config option im_slot)
        if has("srs"):
            im_slot = int(self.config.devices["srs"].options.get("im_slot", 5))
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

        # --- WaveShaper scalar keywords: WSP_PHASE programs 2nd-order
        # dispersion (d2 in ps/nm), WSP_ATTEN a flat attenuation in dB —
        # matching how the old orchestration drove the flattener.
        if has("waveshaper1"):
            bind(
                "LFC_WSP_PHASE",
                getter=lambda: self._softstore.get("LFC_WSP_PHASE", 0.0),
                setter=self._set_wsp_phase,
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

    def _set_wsp_phase(self, d2_ps_nm: float) -> None:
        ws = self.device("waveshaper1")
        ws.set_dispersion(d2_ps_nm=float(d2_ps_nm))
        ws.write_profile()
        self._softstore["LFC_WSP_PHASE"] = float(d2_ps_nm)

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
            rf_oscillator_on=probe(lambda: self.device("rf_osc_psu").output_on(1)),
            rf_amplifier_on=probe(lambda: self.device("rf_amp_psu").output_on(1)),
            # Pendulum counter not ported yet (tier 2); infer from RF chain,
            # as the old check only consulted it when both supplies were on.
            rep_rate_detected=probe(
                lambda: (
                    self.device("rf_osc_psu").output_on(1)
                    and self.device("rf_amp_psu").output_on(1)
                )
            ),
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
