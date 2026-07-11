"""RIO ORION seed laser module.

Binary packet protocol: frames are ``0xA9 ... 0xA5`` with an 8-bit
two's-complement checksum. Temperatures are set/read as thermistor
resistances converted through the Steinhart–Hart equation with the module's
calibration constants. Ported from ``Hardware/ORIONLaser.py`` (which had a
latent bug — the packet ID byte was never actually written; fixed here).

Never probed by discovery: an errant write can change the diode current, so
this device is on the never-probe list (see ``discovery``).
"""

from __future__ import annotations

import contextlib
import math
from typing import ClassVar

import numpy as np

from .base import Instrument
from .errors import ResponseError

__all__ = ["OrionLaser"]

_HEADER, _FOOTER = 0xA9, 0xA5
_MAX_PACKET = 64

# Steinhart-Hart calibration for the module thermistor
_SH_A, _SH_B, _SH_C = 1.2146e-3, 2.1922e-4, 1.5244e-7

# Board temperature sensor lookup (voltage -> degC), from the datasheet
_BOARD_V = [2.273, 2.124, 1.919, 1.667, 1.390, 1.115, 0.867, 0.660, 0.497, 0.372, 0.279, 0.210]
_BOARD_T = [-20.0, -10.0, 0.0, 10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0]

# Command IDs
_CMD_FIRMWARE = 0x01
_CMD_SERIAL = 0x08
_CMD_STATUS = 0x0E
_CMD_THERMISTOR_V = 0x11
_CMD_BOARD_V = 0x12
_CMD_PHOTOMON_V = 0x13
_CMD_CUR_VOLATILE_R = 0x1D
_CMD_CUR_VOLATILE_W = 0x1E
_CMD_TEC_VOLATILE_R = 0x1F
_CMD_TEC_VOLATILE_W = 0x20
_CMD_ENABLE = 0x24
_CMD_DISABLE = 0x25
_CMD_CUR_NONVOL_R = 0x26
_CMD_CUR_NONVOL_W = 0x27
_CMD_TEC_NONVOL_R = 0x28
_CMD_TEC_NONVOL_W = 0x29
_CMD_PRODUCT_ID = 0x42


def _ohm_to_degC(resistance: float) -> float:
    ln_r = math.log(resistance)
    kelvin = 1.0 / (_SH_A + _SH_B * ln_r + _SH_C * ln_r**3)
    return kelvin - 273.15


def _degC_to_ohm(temp_C: float) -> int:
    kelvin = temp_C + 273.15
    x = (_SH_A - 1.0 / kelvin) / _SH_C
    y = math.sqrt((_SH_B / (3 * _SH_C)) ** 3 + x**2 / 4)
    return round(math.exp((y - x / 2) ** (1 / 3) - (y + x / 2) ** (1 / 3)))


def _frame(cmd_id: int, data: bytes = b"", read: bool = True, pkt_id: int = 0) -> bytes:
    """Build one protocol frame (header, length, dest, r/w, cmd, checksum)."""
    length = len(data) + 7
    packet = bytearray(length + 2)
    packet[0] = _HEADER
    packet[1] = pkt_id & 0xFF
    packet[2] = length
    packet[4] = 0xFF  # destination ID
    packet[5] = 0x01 if read else 0x02
    packet[6] = cmd_id & 0xFF
    packet[7 : 7 + len(data)] = data
    packet[-2] = -sum(packet) & 0xFF
    packet[-1] = _FOOTER
    return bytes(packet)


class OrionLaser(Instrument):
    """RIO ORION laser on a VISA serial port. Currents in mA, temps in degC."""

    TRANSPORT_DEFAULTS: ClassVar[dict] = {}

    def _transact(self, cmd_id: int, data: bytes = b"", read: bool = True) -> bytes:
        """Send one frame and return the payload of the reply frame."""

        def op() -> bytes:
            self.transport.write_bytes(_frame(cmd_id, data, read=read))
            return self._read_frame()

        return self._io(op)

    def _read_frame(self) -> bytes:
        # Reply may carry a leading 0x00; scan to the header, then read to
        # the footer. Bounded so a dead line can't loop forever.
        packet = bytearray()
        for _ in range(_MAX_PACKET):
            byte = self.transport.read_bytes(1)
            if not byte:
                break
            if not packet and byte[0] != _HEADER:
                continue  # skip leading garbage / 0x00
            packet += byte
            if len(packet) >= 3 and len(packet) == packet[2] + 2:
                break
        if len(packet) < 9 or packet[0] != _HEADER or packet[-1] != _FOOTER:
            raise ResponseError(f"{self.name}: malformed reply frame {bytes(packet).hex()!r}")
        if sum(packet[:-1]) & 0xFF:
            raise ResponseError(f"{self.name}: checksum error in {bytes(packet).hex()!r}")
        return bytes(packet[8 : packet[2]])

    def _read_word_scaled(self, cmd_id: int) -> float:
        """Read a 16-bit ADC word and scale to volts (2.5 V / 65520)."""
        return int.from_bytes(self._transact(cmd_id), "big") * 2.5 / 65520

    def _configure(self) -> None:
        # Enable communications (old driver sent 0x24 on connect).
        self._read_frame_after_enable()

    def _read_frame_after_enable(self) -> None:
        self.transport.write_bytes(_frame(_CMD_ENABLE, read=False))
        # some firmware versions do not acknowledge enable
        with contextlib.suppress(ResponseError):
            self._read_frame()

    # ------------------------------------------------------------- identity

    @property
    def product_id(self) -> str:
        return self._transact(_CMD_PRODUCT_ID).decode(errors="replace")

    @property
    def serial_number(self) -> int:
        return int.from_bytes(self._transact(_CMD_SERIAL), "big")

    @property
    def firmware_version(self) -> str:
        word = int.from_bytes(self._transact(_CMD_FIRMWARE), "big")
        return f"{(word & 0xF000) >> 12}.{(word & 0x0FF0) >> 4}.{word & 0x000F}"

    # ------------------------------------------------------------- monitors

    @property
    def status_words(self) -> bytes:
        return self._transact(_CMD_STATUS)

    @property
    def photomonitor_V(self) -> float:
        """Photodiode monitor voltage (numerically equal to mA at 1 kOhm)."""
        return self._read_word_scaled(_CMD_PHOTOMON_V)

    @property
    def board_temperature_C(self) -> float:
        v = self._read_word_scaled(_CMD_BOARD_V)
        if not (_BOARD_V[-1] <= v <= _BOARD_V[0]):
            self.log.info("%s: board temp sensor voltage %.3f V out of range", self.name, v)
            return math.nan
        return float(np.interp(v, _BOARD_V[::-1], _BOARD_T[::-1]))

    @property
    def thermistor_temperature_C(self) -> float:
        v = self._read_word_scaled(_CMD_THERMISTOR_V)
        return _ohm_to_degC(v * 10_000 / (2.5 - v))

    # -------------------------------------------------------- diode current

    def diode_current_mA(self, volatile: bool = False) -> float:
        cmd = _CMD_CUR_VOLATILE_R if volatile else _CMD_CUR_NONVOL_R
        return int.from_bytes(self._transact(cmd), "big") / 10

    def set_diode_current_mA(self, mA: float, volatile: bool = True) -> None:
        """Set diode current; volatile settings reset when power-cycled."""
        cmd = _CMD_CUR_VOLATILE_W if volatile else _CMD_CUR_NONVOL_W
        setpoint = int(round(mA * 10)).to_bytes(2, "big")  # units of 0.1 mA
        self._transact(cmd, data=setpoint, read=False)

    # ---------------------------------------------------------- TEC setpoint

    def tec_setpoint_C(self, volatile: bool = False) -> float:
        cmd = _CMD_TEC_VOLATILE_R if volatile else _CMD_TEC_NONVOL_R
        return _ohm_to_degC(int.from_bytes(self._transact(cmd), "big"))

    def set_tec_setpoint_C(self, temp_C: float, volatile: bool = True) -> None:
        cmd = _CMD_TEC_VOLATILE_W if volatile else _CMD_TEC_NONVOL_W
        self._transact(cmd, data=_degC_to_ohm(temp_C).to_bytes(2, "big"), read=False)

    # ---------------------------------------------------------------- misc

    def status(self) -> dict:
        return {
            "photomonitor_V": self.photomonitor_V,
            "board_temperature_C": self.board_temperature_C,
            "thermistor_temperature_C": self.thermistor_temperature_C,
            "diode_current_mA": self.diode_current_mA(volatile=True),
            "tec_setpoint_C": self.tec_setpoint_C(volatile=True),
        }

    # ----------------------------------------------------------------- sim

    @classmethod
    def sim_responses(cls) -> dict:
        state = {
            _CMD_CUR_VOLATILE_R: (1450).to_bytes(2, "big"),  # 145.0 mA
            _CMD_CUR_NONVOL_R: (1450).to_bytes(2, "big"),
            _CMD_TEC_VOLATILE_R: (9661).to_bytes(2, "big"),  # ~25.9 degC
            _CMD_TEC_NONVOL_R: (9661).to_bytes(2, "big"),
        }
        fixed = {
            _CMD_FIRMWARE: (0x1020).to_bytes(2, "big"),  # 1.2.0
            _CMD_SERIAL: (806734).to_bytes(3, "big"),
            _CMD_PRODUCT_ID: b"ORION-SIM",
            _CMD_STATUS: b"\x00\x00\x00",
            # ADC words chosen to land in plausible ranges
            _CMD_BOARD_V: int(1.39 / 2.5 * 65520).to_bytes(2, "big"),  # ~20 degC
            _CMD_THERMISTOR_V: int(1.22 / 2.5 * 65520).to_bytes(2, "big"),
            _CMD_PHOTOMON_V: int(1.0 / 2.5 * 65520).to_bytes(2, "big"),
        }
        writes = {
            _CMD_CUR_VOLATILE_W: _CMD_CUR_VOLATILE_R,
            _CMD_CUR_NONVOL_W: _CMD_CUR_NONVOL_R,
            _CMD_TEC_VOLATILE_W: _CMD_TEC_VOLATILE_R,
            _CMD_TEC_NONVOL_W: _CMD_TEC_NONVOL_R,
        }

        def respond(request: bytes) -> bytes:
            cmd_id = request[6]
            if cmd_id in writes:
                state[writes[cmd_id]] = request[7:9]
                return _sim_reply(cmd_id, b"")
            if cmd_id in (_CMD_ENABLE, _CMD_DISABLE):
                return _sim_reply(cmd_id, b"")
            data = state.get(cmd_id, fixed.get(cmd_id, b"\x00\x00"))
            return _sim_reply(cmd_id, data)

        return {bytes: respond}


def _sim_reply(cmd_id: int, data: bytes) -> bytes:
    """Frame a simulated reply: header/len/.../status/cmd/data/checksum."""
    length = len(data) + 8
    packet = bytearray(length + 2)
    packet[0] = _HEADER
    packet[2] = length
    packet[6] = 0x00  # status OK
    packet[7] = cmd_id
    packet[8 : 8 + len(data)] = data
    packet[-2] = -sum(packet) & 0xFF
    packet[-1] = _FOOTER
    return bytes(packet)
