"""Pritel high-power optical fiber amplifier.

The most safety-critical device on the rack: too little seed power with the
pump on can cause a Q-switch failure that damages the amplifier, compressor,
and octave waveguide (an Arduino latching relay provides the hardware
interlock; see ``arduino_relay``).

Ported from ``Hardware/PritelAmp.py``. Device behaviors preserved:

* **Current ramping.** Large current changes are applied in steps
  (default 100 mA for the preamp, 50 mA for the power amp) so the output
  power changes gradually. This is commissioning-tested behavior — keep it.
* **Hard limits raise** (600 mA preamp, 5800 mA power amp) rather than
  clamping: an out-of-range request is a caller bug, not a device quirk.
* Power-amp setpoints are rounded to 10 mA (the ``FA SETPWR`` command has
  0.01 A resolution).
* Pump on/off commands are re-sent until the readback confirms.

Protocol notes: 9600 baud, LF read termination. Every command is a query;
the unit first echoes a blank/echo line, then the response line, which
starts with a stray control character. :meth:`PritelAmp._ask` handles both.
"""

from __future__ import annotations

import time
from typing import ClassVar

import numpy as np

from .base import Instrument
from .errors import ResponseError
from .transports import SimTransport, Transport

__all__ = ["PritelAmp", "to_mA"]


def to_mA(value: float | str) -> float:
    """Convert ``250``, ``"250"``, ``"250mA"`` or ``"0.5A"`` to mA.

    The old orchestration code passed currents as strings like ``"3.9A"``;
    this helper keeps those call sites easy to port.
    """
    if isinstance(value, int | float):
        return float(value)
    text = str(value).strip()
    lowered = text.casefold()
    if lowered[-2:] in ("ma", "mv", "mw"):
        return float(text[:-2])
    if lowered[-1:] in ("a", "v", "w"):
        return float(text[:-1]) * 1000
    return float(text)


class PritelAmp(Instrument):
    """Pritel fiber amplifier on a VISA serial port.

    All currents in mA, powers in mW.
    """

    # 1 s is generous: replies arrive well under that, and _configure()'s
    # drain loop pays one timeout per connect (was 25 s in the old code,
    # which made every connect take ~25 s).
    TRANSPORT_DEFAULTS: ClassVar[dict] = {
        "timeout_ms": 1_000,
        "baud_rate": 9_600,
        "read_termination": "\n",
        "write_termination": "\r\n",
    }

    PREAMP_MAX_MA: ClassVar[float] = 600.0
    PWRAMP_MAX_MA: ClassVar[float] = 5800.0

    #: ramp step sizes; 0 disables ramping for that stage
    RAMP_STEP_PRE_MA: ClassVar[float] = 100.0
    RAMP_STEP_PWR_MA: ClassVar[float] = 50.0

    PUMP_TIMEOUT_S: ClassVar[float] = 5.0

    def __init__(self, transport: Transport, name: str = ""):
        super().__init__(transport, name)
        # The real unit sends an echo line before the response; SimTransport
        # answers immediately.
        self._discard_echo = not isinstance(transport, SimTransport)

    def _configure(self) -> None:
        # Wake the unit and flush its greeting, like the old connect().
        self.transport.write("READY?")
        if not self._discard_echo:
            self.transport.read()  # sim answers immediately; nothing to flush
            return
        time.sleep(0.5)
        while True:  # drain until a read times out or comes back empty
            try:
                if not self.transport.read():
                    break
            except Exception:  # noqa: BLE001 - flush until quiet
                break

    def _ask(self, cmd: str) -> str:
        """Send a command and return its (cleaned) response line."""
        # A timeout on the echo read means the unit never replied (dropped
        # command); on the response read it means the two-line accounting
        # went wrong (merged/extra line). Report which, to tell them apart.
        stage = "echo read"

        def op() -> str:
            nonlocal stage
            stage = "echo read"
            reply = self.transport.query(cmd)
            if self._discard_echo:
                time.sleep(0.1)
                stage = "response read"
                reply = self.transport.read()
            return reply.lstrip("\r\x00\x11\x13 ").strip()

        return self._io(op, what=lambda: f"{cmd!r} ({stage})")

    @staticmethod
    def _value_after_equals(response: str, what: str) -> float:
        """Parse ``'PreAmp Current = 000 mA'`` style replies to mA/mW."""
        if "=" not in response:
            raise ResponseError(f"Unexpected {what} reply: {response!r}")
        return to_mA(response.split("=")[-1].strip())

    # ------------------------------------------------------------ monitors

    @property
    def input_power_mW(self) -> float:
        return self._value_after_equals(self._ask("FA INPUT?"), "input power")

    @property
    def output_power_mW(self) -> float:
        return self._value_after_equals(self._ask("FA OUTPUT?"), "output power")

    @property
    def auto_shutdown_status(self) -> str:
        """Auto-shutdown (ASD) status text from the unit."""
        return self._ask("FA ASD?")

    # ---------------------------------------------------------------- pump

    @property
    def pump_on(self) -> bool:
        reply = self._ask("FA PUMP?")  # 'Pump ON' / 'Pump OFF'
        state = reply.split(" ")[-1].upper()
        if state not in ("ON", "OFF"):
            raise ResponseError(f"{self.name}: bad pump reply {reply!r}")
        return state == "ON"

    def set_pump(self, on: bool) -> None:
        """Turn the pump on/off, re-sending until the readback confirms."""
        target = bool(on)
        if target:
            self.log.info(
                "%s: ACTIVATING PUMP - seed input power must be appropriate to avoid damage",
                self.name,
            )
        deadline = time.monotonic() + self.PUMP_TIMEOUT_S
        while self.pump_on != target:
            if time.monotonic() > deadline:
                raise RuntimeError(
                    f"{self.name}: pump did not turn {'ON' if target else 'OFF'} "
                    f"within {self.PUMP_TIMEOUT_S:.0f} s"
                )
            self._ask("FA ON" if target else "FA OFF")
        self.log.info("%s: pump %s", self.name, "ON" if target else "OFF")

    # -------------------------------------------------------------- preamp

    @property
    def preamp_mA(self) -> float:
        return self._value_after_equals(self._ask("FA PREAMP?"), "preamp current")

    def set_preamp_mA(self, mA: float, ramp: bool = True) -> None:
        """Set the preamp current, ramping in RAMP_STEP_PRE_MA steps."""
        mA = to_mA(mA)
        if mA > self.PREAMP_MAX_MA:
            raise ValueError(
                f"{self.name}: preamp {mA:.0f} mA exceeds max {self.PREAMP_MAX_MA:.0f} mA"
            )
        for step in self._ramp_steps(self.preamp_mA, mA, self.RAMP_STEP_PRE_MA if ramp else 0):
            reply = self._ask(f"FA SETPRE {step:03.0f}")
            self.log.info("%s: %s", self.name, reply)

    # ------------------------------------------------------------ power amp

    @property
    def pwramp_mA(self) -> float:
        return self._value_after_equals(self._ask("FA PWRAMP?"), "power-amp current")

    def set_pwramp_mA(self, mA: float, ramp: bool = True) -> None:
        """Set the power-amp current, ramping in RAMP_STEP_PWR_MA steps.

        The command resolution is 0.01 A, so values are rounded to 10 mA.
        """
        mA = round(to_mA(mA) / 10) * 10
        if mA > self.PWRAMP_MAX_MA:
            raise ValueError(
                f"{self.name}: power amp {mA:.0f} mA exceeds max {self.PWRAMP_MAX_MA:.0f} mA"
            )
        if not self.pump_on and mA > 0:
            self.log.warning("%s: setting power-amp current with pump OFF has no effect", self.name)
        for step in self._ramp_steps(self.pwramp_mA, mA, self.RAMP_STEP_PWR_MA if ramp else 0):
            reply = self._ask(f"FA SETPWR {step / 10:03.0f}")
            self.log.info("%s: %s (output %.2f W)", self.name, reply, self.output_power_mW / 1e3)

    @staticmethod
    def _ramp_steps(start_mA: float, stop_mA: float, step_mA: float) -> list[float]:
        """Intermediate setpoints from start to stop, rounded to 10 mA.

        Matches the old driver: linspace with ~step_mA spacing, always
        including the final value; a single step when ramping is disabled.
        """
        if step_mA <= 0 or start_mA == stop_mA:
            return [stop_mA]
        n_steps = max(int(np.ceil(abs(stop_mA - start_mA) / step_mA)), 2)
        steps = np.round(np.linspace(start_mA, stop_mA, n_steps) / 10) * 10
        return list(steps)

    # ---------------------------------------------------------------- misc

    def status(self) -> dict:
        return {
            "pump_on": self.pump_on,
            "preamp_mA": self.preamp_mA,
            "pwramp_mA": self.pwramp_mA,
            "input_power_mW": self.input_power_mW,
            "output_power_mW": self.output_power_mW,
            "auto_shutdown": self.auto_shutdown_status,
        }

    # ----------------------------------------------------------------- sim

    @classmethod
    def sim_responses(cls) -> dict:
        import re

        state = {"pump": "OFF", "pre": 0.0, "pwr": 0.0}

        def set_pre(m):
            state["pre"] = float(m.group(1))
            return f"Setting PreAmp Current to {m.group(1)} mA"

        def set_pwr(m):
            state["pwr"] = float(m.group(1)) * 10
            return f"Setting PowerAmp Current to {float(m.group(1)) / 100:.2f} A"

        def set_pump(target):
            def _set(_):
                state["pump"] = target
                return f"Pump {target}"

            return _set

        return {
            "READY?": "PriTel FA Ready",
            "FA INPUT?": "Input Power = 1 mW",
            "FA OUTPUT?": lambda _: f"Output Power = {state['pwr'] / 2000:.2f} W",
            "FA ASD?": "AutoShutDown Enabled. PowerAmp pump current is disabled.",
            "FA PUMP?": lambda _: f"Pump {state['pump']}",
            "FA ON": set_pump("ON"),
            "FA OFF": set_pump("OFF"),
            "FA PREAMP?": lambda _: f"PreAmp Current = {state['pre']:03.0f} mA",
            "FA PWRAMP?": lambda _: f"PowerAmp Current = {state['pwr'] / 1000:.2f} A",
            re.compile(r"FA SETPRE (\d+)$"): set_pre,
            re.compile(r"FA SETPWR (\d+)$"): set_pwr,
        }
