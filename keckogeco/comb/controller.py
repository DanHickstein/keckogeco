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

from ..check import build_device
from ..config import Config
from ..drivers.base import Instrument
from ..drivers.errors import InstrumentError
from . import state as state_mod
from .keywords import KeywordRegistry
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
        self._started = True
        log.info(
            "controller started: %d device(s) online, %d offline%s",
            len(self.devices),
            len(self.offline),
            " [SIM]" if self.sim else "",
        )

    def stop(self) -> None:
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
        if has("ptamp"):
            bind(
                "LFC_PTAMP_PRE_P",
                getter=lambda: self.device("ptamp").preamp_mA,
                setter=lambda v: self.device("ptamp").set_preamp_mA(v),
            )
            bind(
                "LFC_PTAMP_I",
                getter=lambda: self.device("ptamp").pwramp_mA,
                setter=lambda v: self.device("ptamp").set_pwramp_mA(v),
            )
            bind("LFC_PTAMP_OUT", getter=lambda: self.device("ptamp").output_power_mW)
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

        # --- RF chain power supplies
        # NOTE: channel assignments (which GPD channel feeds the oscillator)
        # must be verified on-site; channel 1 is assumed pending that check.
        if has("rf_osc_psu"):
            bind(
                "LFC_RFOSCI_ONOFF",
                getter=lambda: self.device("rf_osc_psu").output_on(1),
                setter=lambda v: self.device("rf_osc_psu").set_output(v, 1),
            )
            bind("LFC_RFOSCI_I", getter=lambda: self.device("rf_osc_psu").output_current_A(1))
            bind("LFC_RFOSCI_V", getter=lambda: self.device("rf_osc_psu").output_voltage_V(1))
        if has("rf_amp_psu"):
            bind(
                "LFC_RFAMP_ONOFF",
                getter=lambda: self.device("rf_amp_psu").output_on(1),
                setter=lambda v: self.device("rf_amp_psu").set_output(v, 1),
            )
            bind("LFC_RFAMP_I", getter=lambda: self.device("rf_amp_psu").output_current_A(1))
            bind("LFC_RFAMP_V", getter=lambda: self.device("rf_amp_psu").output_voltage_V(1))

        # --- TECs
        if has("tec_ppln"):
            bind(
                "LFC_PPLN_T",
                getter=lambda: self.device("tec_ppln").temperature_C,
                setter=lambda v: self.device("tec_ppln").set_temperature_C(v),
            )
        if has("tec_wvg"):
            bind(
                "LFC_WGD_T",
                getter=lambda: self.device("tec_wvg").temperature_C,
                setter=lambda v: self.device("tec_wvg").set_temperature_C(v),
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

        # --- comb state
        bind("LFC_CHECK_STATUS", getter=lambda: state_mod.legacy_code(self.subsystem_status()))
        bind(
            "LFC_CHECK_FULLCOMB",
            getter=lambda: 1 if self.comb_state() == CombState.FULL_COMB else 0,
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
            "sim": self.sim,
        }
