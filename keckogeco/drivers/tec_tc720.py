"""TE Technology TC-720 TEC temperature controller.

Controls the PPLN doubler and octave-waveguide TECs. Protocol (from the
TC-720 manual, via Lars Borm's library that the old 2000-line
``Hardware/TEC_TC720.py`` adapted): ASCII frames

``* CC DDDD SS \\r``  (command, value, checksum)  →  ``* DDDD SS ^``

at 230400 baud, where values are 16-bit two's-complement hex and the
checksum is the modulo-256 sum of the six command/value characters as two
hex digits. Characters are sent one at a time with a 5 ms pause — the
controller drops bytes otherwise. Write commands are acknowledged by
echoing the value and are retried up to 5 times.

Only the surface the comb actually uses is ported: temperatures, setpoint,
mode/control type, and output level.
"""

from __future__ import annotations

import time
from typing import ClassVar

from .base import Instrument
from .errors import ResponseError
from .transports import SimTransport, Transport

__all__ = ["TC720"]

# Command codes (read unless noted)
_CMD_TEMP1 = "01"
_CMD_OUTPUT = "02"
_CMD_TEMP2 = "04"
_CMD_SET_TEMP_W = "1c"  # write
_CMD_MODE_W = "3d"  # write
_CMD_CONTROL_W = "3f"  # write
_CMD_OUTPUT_W = "40"  # write
_CMD_SET_TEMP = "50"
_CMD_MODE = "71"
_CMD_CONTROL = "73"
_CMD_SET_OUTPUT = "74"

MODES = {0: "normal set", 1: "ramp/soak", 2: "proportional+dead band"}
CONTROL_TYPES = {0: "PID", 1: "manual", 2: "analog out"}


def _int_to_hex(value: int) -> str:
    """16-bit two's-complement hex encoding.

    The vendor library encoded negatives as ``2**15 - value``, which does
    not round-trip through its own decoder; standard two's complement does,
    and matches the decoder for all values the comb uses.
    """
    if not -32768 <= value <= 32767:
        raise ValueError(f"value {value} does not fit in 16 bits")
    return f"{value & 0xFFFF:04x}"


def _hex_to_int(data: str) -> int:
    value = int(data, 16)
    if value >= 2**15:
        value -= 2**16
    return value


def _checksum(payload: str) -> str:
    """Modulo-256 sum of the payload characters, as two hex digits."""
    return f"{sum(payload.encode('ascii')) % 256:02x}"


def _build_message(command: str, value: str = "0000") -> str:
    body = command + value
    return "*" + body + _checksum(body) + "\r"


class TC720(Instrument):
    """TC-720 on a plain COM port. Temperatures in degC."""

    DEFAULT_TRANSPORT: ClassVar[str] = "serial"
    TRANSPORT_DEFAULTS: ClassVar[dict] = {"baud_rate": 230_400, "timeout_s": 2.0}

    #: inter-character pause; the controller drops bytes without it
    CHAR_DELAY_S: ClassVar[float] = 0.005
    WRITE_RETRIES: ClassVar[int] = 5

    def __init__(self, transport: Transport, name: str = ""):
        super().__init__(transport, name)
        self._sim = isinstance(transport, SimTransport)

    def _send_raw(self, message: str) -> None:
        if self._sim:
            self.transport.write_bytes(message.encode("ascii"))
            return
        for char in message:
            self.transport.write_bytes(char.encode("ascii"))
            time.sleep(self.CHAR_DELAY_S)

    def _read_reply(self) -> str:
        reply = self.transport.read_bytes(8).decode("ascii", "replace")
        if len(reply) != 8 or reply[0] != "*" or reply[-1] != "^":
            raise ResponseError(f"{self.name}: malformed reply {reply!r}")
        data, checksum = reply[1:5], reply[5:7]
        if checksum != _checksum(data):
            # Manual ambiguity: warn rather than fail so a checksum-scheme
            # mismatch can be diagnosed on real hardware without bricking.
            self.log.warning("%s: reply checksum mismatch in %r", self.name, reply)
        return data

    def _read_value(self, command: str) -> int:
        def op() -> int:
            self.transport.clear()
            self._send_raw(_build_message(command))
            return _hex_to_int(self._read_reply())

        return self._io(op)

    def _write_value(self, command: str, value: int) -> None:
        encoded = _int_to_hex(value)
        message = _build_message(command, encoded)

        def op() -> None:
            for _ in range(self.WRITE_RETRIES):
                self.transport.clear()
                self._send_raw(message)
                if self._read_reply() == encoded:
                    return
                time.sleep(0.05)
            raise ResponseError(f"{self.name}: write {message!r} was never acknowledged")

        self._io(op)

    # ---------------------------------------------------------- temperatures

    @property
    def temperature_C(self) -> float:
        """Sensor 1 temperature."""
        return self._read_value(_CMD_TEMP1) / 100

    @property
    def temperature2_C(self) -> float:
        """Sensor 2 temperature."""
        return self._read_value(_CMD_TEMP2) / 100

    @property
    def setpoint_C(self) -> float:
        return self._read_value(_CMD_SET_TEMP) / 100

    def set_temperature_C(self, temperature: float) -> None:
        """Set and hold a temperature (requires 'normal set' mode)."""
        mode = self.mode
        if mode != 0:
            raise RuntimeError(
                f"{self.name}: set_temperature requires mode 0 (normal set); "
                f"device is in mode {mode} ({MODES.get(mode, '?')})"
            )
        self._write_value(_CMD_SET_TEMP_W, int(round(temperature * 100)))
        self.log.info("%s: setpoint -> %.2f C", self.name, temperature)

    # ------------------------------------------------------- mode and output

    @property
    def mode(self) -> int:
        """0 = normal set, 1 = ramp/soak, 2 = proportional+dead band."""
        return self._read_value(_CMD_MODE)

    def set_mode(self, mode: int) -> None:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {sorted(MODES)}")
        self._write_value(_CMD_MODE_W, mode)

    @property
    def control_type(self) -> int:
        """0 = PID, 1 = manual, 2 = analog out (in mode 0 only)."""
        return self._read_value(_CMD_CONTROL)

    def set_control_type(self, control_type: int) -> None:
        if control_type not in CONTROL_TYPES:
            raise ValueError(f"control_type must be one of {sorted(CONTROL_TYPES)}")
        self._write_value(_CMD_CONTROL_W, control_type)

    @property
    def output(self) -> int:
        """Present output level, -511..511."""
        return self._read_value(_CMD_OUTPUT)

    def set_output(self, output: int) -> None:
        """Manual output level, -511..511 (control type 1)."""
        if not -511 <= output <= 511:
            raise ValueError(f"output {output} outside -511..511")
        self._write_value(_CMD_OUTPUT_W, output)

    def status(self) -> dict:
        return {
            "temperature_C": self.temperature_C,
            "temperature2_C": self.temperature2_C,
            "setpoint_C": self.setpoint_C,
            "output": self.output,
            "mode": self.mode,
        }

    # ----------------------------------------------------------------- sim

    @classmethod
    def sim_responses(cls) -> dict:
        state = {"set_temp": 2500, "mode": 0, "control": 0, "output": 0}
        reads = {
            _CMD_TEMP1: lambda: state["set_temp"],  # sits at setpoint
            _CMD_TEMP2: lambda: 2312,
            _CMD_SET_TEMP: lambda: state["set_temp"],
            _CMD_MODE: lambda: state["mode"],
            _CMD_CONTROL: lambda: state["control"],
            _CMD_OUTPUT: lambda: state["output"],
            _CMD_SET_OUTPUT: lambda: state["output"],
        }
        writes = {
            _CMD_SET_TEMP_W: "set_temp",
            _CMD_MODE_W: "mode",
            _CMD_CONTROL_W: "control",
            _CMD_OUTPUT_W: "output",
        }

        def respond(raw: bytes) -> bytes:
            message = raw.decode("ascii", "replace")
            command, value = message[1:3], message[3:7]
            if command in writes:
                state[writes[command]] = _hex_to_int(value)
                data = value  # write ack echoes the value
            elif command in reads:
                data = _int_to_hex(reads[command]())
            else:
                data = "0000"
            return ("*" + data + _checksum(data) + "^").encode("ascii")

        return {bytes: respond}
