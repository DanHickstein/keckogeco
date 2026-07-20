"""Arduino latching-relay interlock for the Pritel amplifier.

The Arduino sits in series with an analog relay on the Pritel interlock
input: if the seed power drops, the analog relay kills the pump within
milliseconds, and the Arduino latch prevents it from re-enabling until a
manual ``reset``. It also drives the YJ shutter. Firmware source lives in
the old repo under ``Arduino Codes/``.

Ported from ``Hardware/Arduino_relay.py``. Protocol: plain serial at 9600
baud; commands are single words; replies are one or more text lines that
arrive over ~0.2 s (no terminator discipline), so queries drain whatever
bytes arrived and retry on silence. The interactive stdin confirmations of
the old threshold setters are replaced by ``force=True``.

The board is an Arduino Uno, and a port open that asserts DTR auto-resets
it (DTR is capacitor-coupled to the MCU reset pin). The firmware boots
latched-tripped — killing the Pritel pump through the hardware enable
line — with thresholds reverted to compiled defaults and the YJ shutter
back at "passing". The transport therefore holds DTR/RTS de-asserted
(rack-verified 2026-07-20: latch, thresholds, and shutter state survive
server restarts). The latch still trips on a genuine power cycle, which
is the fail-safe the firmware intends.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import ClassVar

from .base import Instrument
from .errors import ResponseError
from .transports import SimTransport, Transport

__all__ = ["ArduinoRelay", "RelayStatus"]

_ADC_MAX = 1023  # thresholds and readings are raw 10-bit ADC counts (0-5 V)


@dataclass(frozen=True)
class RelayStatus:
    """Parsed output of the firmware's ``get`` command (raw ADC counts)."""

    low_threshold: int
    high_threshold: int
    voltage_judge_low: int
    voltage_judge_high: int
    voltage_now: int

    @property
    def ok_to_amplify(self) -> bool:
        """True when the relay is passing the OK-to-amplify signal.

        Matches the old ``get_relay_status`` logic exactly — never report
        OK unless the latch state really allows amplification.
        """
        return (
            self.voltage_judge_low > self.low_threshold
            and self.voltage_judge_high < self.high_threshold
        )

    @property
    def resettable(self) -> bool:
        """True when a ``reset`` would restore the OK signal."""
        return (
            not self.ok_to_amplify and self.low_threshold < self.voltage_now < self.high_threshold
        )

    def describe(self) -> str:
        if self.ok_to_amplify:
            return "relay sending OK_to_Amplify signal to amplifier"
        if self.resettable:
            return "relay is STOPPING amplifier; OK after reset_latch()"
        if self.voltage_now <= self.low_threshold:
            return "relay is STOPPING amplifier: input power too low"
        if self.voltage_now >= self.high_threshold:
            return "relay is STOPPING amplifier: input power too high"
        return "relay is STOPPING amplifier"


class ArduinoRelay(Instrument):
    """Arduino relay/YJ-shutter controller on a plain COM port."""

    DEFAULT_TRANSPORT: ClassVar[str] = "serial"
    #: DTR/RTS held de-asserted so opening the port does not reset the
    #: board (see module docstring); a config block can override
    TRANSPORT_DEFAULTS: ClassVar[dict] = {
        "baud_rate": 9600,
        "timeout_s": 1.0,
        "dtr": False,
        "rts": False,
    }

    #: post-reset firmware boot time; only waited for when an open can
    #: reset the board (config override re-asserting DTR)
    BOOT_DELAY_S: ClassVar[float] = 2.0
    #: replies dribble out with no terminator; wait this long before draining
    RESPONSE_DELAY_S: ClassVar[float] = 0.2
    MAX_QUERY_TRIES: ClassVar[int] = 10

    def __init__(self, transport: Transport, name: str = ""):
        super().__init__(transport, name)
        self._sim = isinstance(transport, SimTransport)

    def _configure(self) -> None:
        if self._sim:
            return
        # a DTR-suppressed open (the default) does not reset the board,
        # so there is no boot to wait out
        if getattr(self.transport, "dtr", None) is not False:
            time.sleep(self.BOOT_DELAY_S)

    def _ask(self, cmd: str) -> str:
        """Send a command, drain the multi-line reply, retry on silence."""

        def op() -> str:
            for attempt in range(1, self.MAX_QUERY_TRIES + 1):
                self.transport.write(cmd)
                if not self._sim:
                    time.sleep(self.RESPONSE_DELAY_S)
                reply = self.transport.read_available().decode("utf-8", "replace").strip()
                if reply:
                    return reply
                self.log.debug("%s: no reply to %r (try %d)", self.name, cmd, attempt)
            raise ResponseError(
                f"{self.name}: no reply to {cmd!r} after {self.MAX_QUERY_TRIES} tries"
            )

        return self._io(op)

    # ---------------------------------------------------------------- relay

    def relay_info(self) -> str:
        """Raw multi-line status text from the firmware."""
        return self._ask("get")

    def relay_status(self) -> RelayStatus:
        text = self.relay_info()

        def grab(label: str) -> int:
            match = re.search(re.escape(label) + r"\s*(?:is)?\s*(-?\d+)", text, re.IGNORECASE)
            if match is None:
                raise ResponseError(f"{self.name}: {label!r} missing in relay info: {text!r}")
            return int(match.group(1))

        return RelayStatus(
            low_threshold=grab("Low threshold"),
            high_threshold=grab("High threshold"),
            voltage_judge_low=grab("Now voltage to judge (low)"),
            voltage_judge_high=grab("Now voltage to judge (high)"),
            voltage_now=grab("Now voltage"),
        )

    def reset_latch(self) -> str:
        """Clear the latch after a trip (only effective if power is back)."""
        self.log.info("%s: resetting interlock latch", self.name)
        return self._ask("reset")

    def set_low_threshold(self, counts: int, force: bool = False) -> str:
        """Set the low ADC threshold (0-1023). counts<100 requires force."""
        if not 0 <= counts <= _ADC_MAX:
            raise ValueError(f"threshold {counts} outside 0..{_ADC_MAX}")
        if counts < 100 and not force:
            raise ValueError(
                f"low threshold {counts} risks false OK signal; pass force=True to override"
            )
        return self._ask(f"THRESHOLD {counts}")

    def set_high_threshold(self, counts: int, force: bool = False) -> str:
        """Set the high ADC threshold (0-1023). counts>900 requires force."""
        if not 0 <= counts <= _ADC_MAX:
            raise ValueError(f"threshold {counts} outside 0..{_ADC_MAX}")
        if counts > 900 and not force:
            raise ValueError(
                f"high threshold {counts} risks false OK signal; pass force=True to override"
            )
        return self._ask(f"HIGH {counts}")

    # ------------------------------------------------------------ YJ shutter

    def yj_state(self) -> str:
        return self._ask("YJState")

    @property
    def yj_open(self) -> bool:
        return "passing" in self.yj_state().casefold()

    def open_yj(self) -> str:
        return self._ask("YJPass")

    def close_yj(self) -> str:
        return self._ask("YJShut")

    # ---------------------------------------------------------------- misc

    def firmware_help(self) -> str:
        return self._ask("help")

    def status(self) -> dict:
        relay = self.relay_status()
        return {
            "ok_to_amplify": relay.ok_to_amplify,
            "resettable": relay.resettable,
            "description": relay.describe(),
            "voltage_now": relay.voltage_now,
            "yj_open": self.yj_open,
        }

    # ----------------------------------------------------------------- sim

    @classmethod
    def sim_responses(cls) -> dict:
        state = {"low": 300, "high": 900, "v": 500, "yj": "passing"}

        def info(_):
            return (
                f"Low threshold is {state['low']}\n"
                f"High threshold is {state['high']}\n"
                f"Now voltage to judge (low) is {state['v']}\n"
                f"Now voltage to judge (high) is {state['v']}\n"
                f"Now voltage is {state['v']}"
            )

        def set_low(m):
            state["low"] = int(m.group(1))
            return f"Low threshold set to {state['low']}"

        def set_high(m):
            state["high"] = int(m.group(1))
            return f"High threshold set to {state['high']}"

        def yj(target):
            def _set(_):
                state["yj"] = target
                return f"YJ shutter is {target}"

            return _set

        return {
            "get": info,
            "GET": info,
            "reset": "Latch reset",
            "help": "Commands: get reset YJShut YJPass YJState THRESHOLD HIGH",
            "YJState": lambda _: f"YJ shutter is {state['yj']}",
            "YJPass": yj("passing"),
            "YJShut": yj("shutted"),
            re.compile(r"THRESHOLD (\d+)$"): set_low,
            re.compile(r"HIGH (\d+)$"): set_high,
        }
