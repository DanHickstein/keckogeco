"""Amonics erbium-doped fiber amplifier (EDFA).

The KeckLFC rack has three of these (13, 23, and 27 dBm units); each is one
``[devices.*]`` config block using this driver. The protocol is SCPI-like
over VISA serial at 19200 baud with CRLF terminations.

Ported from ``Hardware/AmonicsEDFA.py`` (Maodong Gao, tested on
AEDFA-PA-30-B-FA No. 21020811). Device behaviors preserved from the old
driver:

* On/off commands must be re-sent until the readback confirms the change
  (the unit sometimes ignores the first command), bounded by a timeout.
* Setpoints above the unit's maximum are clamped to the maximum.
* Master activation must not be enabled while the channel is off.
* Some units do not support the input-power monitor query.
"""

from __future__ import annotations

import math
import re
import time
from typing import ClassVar

from .base import Instrument
from .errors import ResponseError

__all__ = ["AmonicsEDFA"]

_CHANNEL_STATES = {"0": "OFF", "1": "ON", "2": "BUSY", "4": "LOCK"}


def _as_switch(value: str | int | bool) -> str:
    """Normalize 0/1/'on'/'off'/bool to the '0'/'1' the device expects."""
    text = str(value).strip().casefold()
    if text in ("1", "on", "true"):
        return "1"
    if text in ("0", "off", "false"):
        return "0"
    raise ValueError(f"Expected 0/1/'on'/'off', got {value!r}")


class AmonicsEDFA(Instrument):
    """Amonics EDFA on a VISA serial port.

    Parameters
    ----------
    transport : Transport
    name : str
        Instance name, e.g. ``"edfa27"``.

    Notes
    -----
    All currents are in mA and powers in mW, matching the front panel.
    Channel arguments default to 1; only some units have a second channel.
    """

    TRANSPORT_DEFAULTS: ClassVar[dict] = {
        "timeout_ms": 25_000,
        "baud_rate": 19_200,
        "read_termination": "\r\n",
        "write_termination": "\r\n",
    }

    #: seconds to keep re-sending a state change before giving up
    STATE_CHANGE_TIMEOUT_S: ClassVar[float] = 3.0

    # ------------------------------------------------------------- identity

    @property
    def model(self) -> str:
        return self.query(":CAL:SYS:MODEL?")

    @property
    def serial_number(self) -> str:
        return self.query(":CAL:SYS:SERIAL?")

    @property
    def interlocked(self) -> bool:
        """True when the hardware interlock is engaged (output inhibited)."""
        response = self.query(":DRIV:INTERLOCK?")
        if response not in ("0", "1"):
            raise ResponseError(f"{self.name}: bad interlock reply {response!r}")
        return response == "1"

    @property
    def case_temperature_C(self) -> float:
        return float(self.query(":SENS:TEMP:BOX?"))

    # ----------------------------------------------------------- monitoring

    def output_power_mW(self, channel: int = 1) -> float:
        return float(self.query(f":SENS:POW:OUT:CH{channel}?"))

    def input_power_mW(self, channel: int = 1) -> float:
        """Seed input power; NaN on units without an input monitor."""
        try:
            return float(self.query(f":SENS:POW:IN:CH{channel}?"))
        except (ValueError, ResponseError):
            self.log.debug("%s: input power monitor not supported", self.name)
            return math.nan

    def pd_power_mW(self, channel: int = 1) -> float:
        """Internal photodiode power."""
        return float(self.query(f":SENS:POW:PD:CH{channel}?"))

    # ----------------------------------------------------------------- mode

    def mode(self, channel: int = 1) -> str:
        """Control mode, ``"ACC"`` (current) or ``"APC"`` (power).

        The AEDFA-PA-30-B-FA at Keck does not answer the mode query; the old
        driver fell back to ACC and we preserve that.
        """
        try:
            reply = self.query(f":MODE:SW:CH{channel}?").upper()
            return reply if reply in ("ACC", "APC") else "ACC"
        except ResponseError:
            return "ACC"

    def set_mode(self, mode: str, channel: int = 1) -> None:
        mode = str(mode).upper()
        if mode not in ("ACC", "APC"):
            raise ValueError(f"Mode must be 'ACC' or 'APC', got {mode!r}")
        self.write(f":MODE:SW:CH{channel} {mode}")

    # ------------------------------------------------------------- setpoint

    def setpoint(self, channel: int = 1) -> float:
        """Channel setpoint: mA in ACC mode, mW in APC mode."""
        mode = self.mode(channel)
        value = float(self.query(f":DRIV:{mode}:CUR:CH{channel}?"))
        unit = self.query(f":READ:DRIV:UNIT:{mode}:CH{channel}?").casefold()
        if unit in ("a", "w"):  # convert to mA / mW
            value *= 1000
        return value

    def max_setpoint(self, channel: int = 1) -> float:
        mode = self.mode(channel)
        return float(self.query(f":READ:DRIV:MAX:{mode}:CH{channel}?"))

    def set_setpoint(self, value: float, channel: int = 1) -> float:
        """Set the channel current (ACC, mA) or power (APC, mW).

        Values above the unit maximum are clamped, matching the old driver.
        Returns the readback value.
        """
        mode = self.mode(channel)
        value = abs(float(value))
        maximum = self.max_setpoint(channel)
        if value > maximum:
            self.log.warning(
                "%s: CH%d setpoint %.1f exceeds maximum %.1f; clamping",
                self.name,
                channel,
                value,
                maximum,
            )
            value = maximum
        self.write(f":DRIV:{mode}:CUR:CH{channel} {value}")
        readback = self.setpoint(channel)
        if abs(readback - value) >= 0.5:
            self.log.warning(
                "%s: CH%d setpoint %.1f read back as %.1f", self.name, channel, value, readback
            )
        return readback

    # ------------------------------------------------------- channel on/off

    def channel_state(self, channel: int = 1) -> str:
        """One of ``OFF | ON | BUSY | LOCK``."""
        reply = self.query(f":DRIV:{self.mode(channel)}:STAT:CH{channel}?")
        state = _CHANNEL_STATES.get(reply)
        if state is None:
            raise ResponseError(f"{self.name}: bad channel state reply {reply!r}")
        return state

    def set_channel(self, on: str | int | bool, channel: int = 1) -> None:
        """Turn a channel on or off, re-sending until the readback confirms."""
        target = "ON" if _as_switch(on) == "1" else "OFF"
        if target == "OFF" and self.activation:
            self.log.warning(
                "%s: turning CH%d off with activation on will drop activation",
                self.name,
                channel,
            )
        mode = self.mode(channel)
        self._retry_until(
            command=f":DRIV:{mode}:STAT:CH{channel} {_as_switch(on)}",
            readback=lambda: self.channel_state(channel),
            target=target,
            what=f"CH{channel} state",
        )

    # ----------------------------------------------------- master activation

    @property
    def activation(self) -> bool:
        """Master activation (the front-panel Activate button)."""
        reply = self.query(":DRIV:MCTRL?")
        if reply not in ("0", "1"):
            raise ResponseError(f"{self.name}: bad activation reply {reply!r}")
        return reply == "1"

    def activate(self) -> None:
        """Enable laser output.

        Refuses if CH1 is off (matches the interlock logic in the old
        driver: activation with the pump channel off trips an error state).
        """
        if self.channel_state(1) == "OFF":
            raise RuntimeError(
                f"{self.name}: cannot activate while CH1 is OFF; turn the channel on first"
            )
        self.log.info(
            "%s: ACTIVATING LASER OUTPUT - seed input power must be appropriate", self.name
        )
        self._retry_until(
            command=":DRIV:MCTRL 1",
            readback=lambda: "ON" if self.activation else "OFF",
            target="ON",
            what="activation",
        )

    def deactivate(self) -> None:
        self._retry_until(
            command=":DRIV:MCTRL 0",
            readback=lambda: "ON" if self.activation else "OFF",
            target="OFF",
            what="activation",
        )

    # ---------------------------------------------------------------- misc

    def status(self) -> dict:
        """Snapshot of everything a GUI panel or keyword read needs."""
        return {
            "interlocked": self.interlocked,
            "case_temperature_C": self.case_temperature_C,
            "activation": self.activation,
            "mode": self.mode(),
            "channel_state": self.channel_state(),
            "setpoint": self.setpoint(),
            "output_power_mW": self.output_power_mW(),
            "input_power_mW": self.input_power_mW(),
        }

    def _retry_until(self, command: str, readback, target: str, what: str) -> None:
        """Re-send `command` until `readback() == target` or timeout.

        The Amonics units sometimes ignore the first state-change command;
        the old driver's send-check-resend loop is preserved here.
        """
        deadline = time.monotonic() + self.STATE_CHANGE_TIMEOUT_S
        while readback() != target:
            if time.monotonic() > deadline:
                raise RuntimeError(
                    f"{self.name}: {what} did not reach {target} "
                    f"within {self.STATE_CHANGE_TIMEOUT_S:.0f} s"
                )
            self.write(command)
        self.log.info("%s: %s set to %s", self.name, what, target)

    # ----------------------------------------------------------------- sim

    @classmethod
    def sim_responses(cls) -> dict:
        """Stateful sim table: writes to setpoint/state/MCTRL read back."""
        state = {"mctrl": "0", "stat": {"1": "0", "2": "0"}, "cur": {"1": "0.0", "2": "0.0"}}

        def set_mctrl(m):
            state["mctrl"] = m.group(1)
            return ""

        def set_stat(m):
            state["stat"][m.group(2)] = m.group(3)
            return ""

        def set_cur(m):
            state["cur"][m.group(2)] = m.group(3)
            return ""

        return {
            ":CAL:SYS:MODEL?": "AEDFA-SIM-30-B-FA",
            ":CAL:SYS:SERIAL?": "00000000",
            ":DRIV:INTERLOCK?": "0",
            ":SENS:TEMP:BOX?": "35.5",
            ":DRIV:MCTRL?": lambda _: state["mctrl"],
            re.compile(r":DRIV:MCTRL (\d)$"): set_mctrl,
            re.compile(r":DRIV:(ACC|APC):STAT:CH(\d)\?"): lambda m: state["stat"][m.group(2)],
            re.compile(r":DRIV:(ACC|APC):STAT:CH(\d) (\d)$"): set_stat,
            re.compile(r":DRIV:(ACC|APC):CUR:CH(\d)\?"): lambda m: state["cur"][m.group(2)],
            re.compile(r":DRIV:(ACC|APC):CUR:CH(\d) ([\d.]+)$"): set_cur,
            re.compile(r":READ:DRIV:UNIT:\w+:CH\d\?"): "mA",
            re.compile(r":READ:DRIV:MAX:\w+:CH\d\?"): "1500.0",
            re.compile(r":SENS:POW:OUT:CH\d\?"): "150.0",
            re.compile(r":SENS:POW:IN:CH\d\?"): "1.2",
            re.compile(r":SENS:POW:PD:CH\d\?"): "10.0",
            re.compile(r":MODE:SW:CH\d\?"): "ACC",
        }
