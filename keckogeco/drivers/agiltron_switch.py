"""Agiltron SelfAlign 2x2 fiber switch.

Binary 4-byte frame protocol on a plain COM port at 9600 baud:

* ``01 06 00 00`` — mode/version query (reply ``01 06 15 1f`` on this unit)
* ``01 13 00 00`` — position query, reply ``01 13 <pos> 00``
* ``01 14 00 <pos>`` — set position, echoed as ``01 14 <pos> 00``

Position 1 routes the YJ path, position 2 the HK path (KTL keyword
``LFC_2BY2_SWITCH``). Ported from ``Hardware/Agiltron_2by2_switch.py``;
the 1x6 SelfAlign is deliberately not ported.
"""

from __future__ import annotations

from typing import ClassVar

from .base import Instrument
from .errors import ResponseError
from .transports import Transport

__all__ = ["AgiltronSwitch2x2"]


class AgiltronSwitch2x2(Instrument):
    """Agiltron 2x2 switch; ``position`` is 1 (YJ) or 2 (HK)."""

    DEFAULT_TRANSPORT: ClassVar[str] = "serial"
    TRANSPORT_DEFAULTS: ClassVar[dict] = {
        "baud_rate": 9_600,
        "timeout_s": 2.0,
    }

    POSITIONS: ClassVar[dict[int, str]] = {1: "YJ", 2: "HK"}

    def __init__(self, transport: Transport, name: str = ""):
        super().__init__(transport, name)

    def _exchange(self, frame: bytes) -> bytes:
        """Send a 4-byte frame; return the 4-byte reply (header-checked)."""

        def op() -> bytes:
            self.transport.clear()
            self.transport.write_bytes(frame)
            reply = self.transport.read_bytes(4)
            if len(reply) < 4 or reply[:2] != frame[:2]:
                raise ResponseError(f"{self.name}: sent {frame.hex()}, got {reply.hex()!r}")
            return reply

        return self._io(op)

    def _configure(self) -> None:
        """Confirm the unit answers the mode/version query after opening."""
        reply = self._exchange(b"\x01\x06\x00\x00")
        self.log.debug("%s: mode/version bytes %s", self.name, reply[2:].hex())

    @property
    def position(self) -> int:
        """Current switch position: 1 = YJ path, 2 = HK path."""
        reply = self._exchange(b"\x01\x13\x00\x00")
        pos = reply[2]
        if pos not in self.POSITIONS:
            raise ResponseError(f"{self.name}: bad position byte {pos} in {reply.hex()!r}")
        return pos

    def set_position(self, position: int) -> None:
        """Switch to ``position`` (1 = YJ, 2 = HK); no-op if already there."""
        position = int(position)
        if position not in self.POSITIONS:
            raise ValueError(f"position must be 1 (YJ) or 2 (HK), got {position}")
        if self.position == position:
            return
        reply = self._exchange(b"\x01\x14\x00" + bytes([position]))
        if reply[2] != position:
            raise ResponseError(
                f"{self.name}: switch did not confirm position {position} ({reply.hex()!r})"
            )
        self.log.info(
            "%s: switched to position %d (%s)", self.name, position, self.POSITIONS[position]
        )

    def status(self) -> dict:
        pos = self.position
        return {"position": pos, "route": self.POSITIONS[pos]}

    @classmethod
    def sim_responses(cls) -> dict:
        state = {"pos": 1}

        def respond(data: bytes) -> bytes:
            if data[:2] == b"\x01\x06":
                return b"\x01\x06\x15\x1f"
            if data[:2] == b"\x01\x13":
                return bytes([0x01, 0x13, state["pos"], 0x00])
            if data[:2] == b"\x01\x14":
                state["pos"] = data[3]
                return bytes([0x01, 0x14, data[3], 0x00])
            return b""

        return {bytes: respond}
