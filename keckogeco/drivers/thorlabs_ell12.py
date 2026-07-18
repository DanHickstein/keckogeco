"""Thorlabs ELL12 six-position filter slider (Elliptec protocol).

The ND-filter output attenuator of the spectral flattener: six discrete
slots, position 6 the 0 dB reference and positions 1-5 holding ND filters
(~5 / ~10 / ~20 / >20 / >20 dB — measured table in
``docs/user_guide/menlo_flattener.md``).

Protocol (Thorlabs "Elliptec communication protocol"): ASCII multi-drop
frames of ``<bus address char><2-letter mnemonic><data>`` at 9600 baud,
CRLF-terminated. Mnemonics used here: ``in`` (device info), ``gs``
(status), ``gp`` (get position), ``ma`` (move absolute), ``ho`` (home).
Positions travel in encoder pulses encoded as 8 uppercase hex chars
(32-bit two's complement); the info reply carries the travel (mm) and
pulses/mm, from which the slot spacing derives (travel spans the six
slots, so spacing = travel / 5).

.. warning::
   The slider is not yet wired to the control laptop — it currently hangs
   off the Menlo laptop's ELLO software — so this driver follows the
   protocol manual and the ``thorlabs-elliptec`` reference implementation
   but is unverified on the real unit. Every move is verified by a
   position read-back, so a wrong slot-spacing derivation fails loudly;
   if that happens on-site, set ``slot_pulses = <n>`` in the device's
   config block to pin the spacing.
"""

from __future__ import annotations

import re
import time
from typing import ClassVar

from .base import Instrument
from .errors import ResponseError
from .transports import Transport

__all__ = ["ThorlabsELL12"]

#: Elliptec GS status codes (thorlabs-elliptec's ELLStatus table)
STATUS_CODES = {
    0: "ok",
    1: "communication timeout",
    2: "mechanical timeout",
    3: "command not supported",
    4: "value out of range",
    5: "module isolated",
    6: "module out of isolation",
    7: "initialization error",
    8: "thermal error",
    9: "busy",
    10: "sensor error",
    11: "motor error",
    12: "out of range",
    13: "over current",
}


class ThorlabsELL12(Instrument):
    """Thorlabs ELL12 slider; ``position`` is the slot number 1..6."""

    DEFAULT_TRANSPORT: ClassVar[str] = "serial"
    TRANSPORT_DEFAULTS: ClassVar[dict] = {
        "baud_rate": 9_600,
        # a full-travel move on the resonant motor is well under a second
        "timeout_s": 3.0,
        "terminator": "\r\n",
    }
    #: Elliptec multi-drop bus address ('0'-'F'); slot_pulses pins the
    #: pulses-between-slots if the derivation from the info reply is wrong
    DRIVER_OPTIONS: ClassVar[tuple[str, ...]] = ("bus_address", "slot_pulses")

    POSITIONS: ClassVar[int] = 6
    #: device type reported by the ELL12 in the info reply (0x0C = 12)
    DEVICE_TYPE: ClassVar[int] = 0x0C
    #: a read-back farther than this fraction of a slot from the nearest
    #: slot center means "between slots" (position unknown)
    SLOT_TOLERANCE: ClassVar[float] = 0.25
    #: post-move polls while the unit answers GS busy
    BUSY_POLLS: ClassVar[int] = 10
    BUSY_POLL_S: ClassVar[float] = 0.3

    def __init__(
        self,
        transport: Transport,
        name: str = "",
        bus_address: str = "0",
        slot_pulses: int | None = None,
    ):
        super().__init__(transport, name)
        self._addr = str(bus_address).upper()
        if len(self._addr) != 1:
            raise ValueError(f"bus_address must be one char '0'-'F', got {bus_address!r}")
        self._slot_pulses = int(slot_pulses) if slot_pulses is not None else None
        self._slot_pulses_from_config = slot_pulses is not None
        self.info: dict = {}

    # -------------------------------------------------------------- protocol

    def _exchange(self, command: str) -> str:
        """Send ``<addr><command>``; return the reply for this address."""

        def op() -> str:
            self.transport.clear()
            self.transport.write(f"{self._addr}{command}")
            reply = self.transport.read()
            if not reply.startswith(self._addr):
                raise ResponseError(f"{self.name}: sent {command!r}, got {reply!r}")
            return reply

        return self._io(op, what=command)

    def _parse_position_pulses(self, reply: str, context: str) -> int:
        """Decode a ``<addr>PO<8 hex>`` reply (32-bit two's complement)."""
        if reply[1:3] == "GS":
            code = int(reply[3:5], 16)
            raise ResponseError(
                f"{self.name}: {context} failed with status {code} "
                f"({STATUS_CODES.get(code, 'unknown')})"
            )
        if reply[1:3] != "PO" or len(reply) < 11:
            raise ResponseError(f"{self.name}: bad position reply {reply!r} for {context}")
        pulses = int(reply[3:11], 16)
        if pulses >= 1 << 31:
            pulses -= 1 << 32
        return pulses

    def _configure(self) -> None:
        """Read the info block; derive the pulses between adjacent slots."""
        reply = self._exchange("in")
        if reply[1:3] != "IN" or len(reply) < 33:
            raise ResponseError(f"{self.name}: bad info reply {reply!r}")
        self.info = {
            "device_type": int(reply[3:5], 16),
            "serial": reply[5:13],
            "year": reply[13:17],
            "firmware": reply[17:19],
            "travel_mm": int(reply[21:25], 16),
            "pulses_per_mm": int(reply[25:33], 16),
        }
        if self.info["device_type"] != self.DEVICE_TYPE:
            self.log.warning(
                "%s: device type 0x%02X is not the expected ELL12 (0x%02X) — "
                "check the config block",
                self.name,
                self.info["device_type"],
                self.DEVICE_TYPE,
            )
        if not self._slot_pulses_from_config:
            derived = round(
                self.info["travel_mm"] * self.info["pulses_per_mm"] / (self.POSITIONS - 1)
            )
            if derived <= 0:
                raise ResponseError(
                    f"{self.name}: cannot derive slot spacing from info reply {reply!r}; "
                    "set slot_pulses in the config block"
                )
            self._slot_pulses = derived
        self.log.debug(
            "%s: serial %s, travel %d mm, %d pulses/mm, %d pulses/slot",
            self.name,
            self.info["serial"],
            self.info["travel_mm"],
            self.info["pulses_per_mm"],
            self._slot_pulses,
        )

    def _pulses_to_slot(self, pulses: int) -> int | None:
        """Nearest slot 1..N, or None when between slots (not homed /
        mid-move / wrong spacing)."""
        slot = round(pulses / self._slot_pulses) + 1
        if not 1 <= slot <= self.POSITIONS:
            return None
        offset = abs(pulses - (slot - 1) * self._slot_pulses)
        if offset > self._slot_pulses * self.SLOT_TOLERANCE:
            return None
        return slot

    def _finish_move(self, reply: str, context: str) -> int:
        """Position pulses from a move reply, waiting out GS busy.

        Moves normally answer with the final position (PO); if the unit
        answers with a status instead, poll ``gs`` until busy clears and
        read the position then — polling ``gp`` mid-move would hand the
        verify a between-slots position.
        """
        for _ in range(self.BUSY_POLLS):
            if reply[1:3] != "GS":
                return self._parse_position_pulses(reply, context)
            code = int(reply[3:5], 16)
            if code == 0:
                return self._parse_position_pulses(self._exchange("gp"), context)
            if code != 9:  # anything but busy is a real fault
                return self._parse_position_pulses(reply, context)
            time.sleep(self.BUSY_POLL_S)
            reply = self._exchange("gs")
        raise ResponseError(f"{self.name}: {context} still busy after {self.BUSY_POLLS} polls")

    # ------------------------------------------------------------------- API

    @property
    def position(self) -> int | None:
        """Current slot 1..6; None when between slots (e.g. not homed)."""
        pulses = self._parse_position_pulses(self._exchange("gp"), "gp")
        return self._pulses_to_slot(pulses)

    def set_position(self, position: int) -> None:
        """Move to slot ``position`` (1..6) and verify the read-back."""
        position = int(position)
        if not 1 <= position <= self.POSITIONS:
            raise ValueError(f"position must be 1..{self.POSITIONS}, got {position}")
        target = (position - 1) * self._slot_pulses
        pulses = self._finish_move(self._exchange(f"ma{target & 0xFFFFFFFF:08X}"), "move")
        landed = self._pulses_to_slot(pulses)
        if landed != position:
            raise ResponseError(
                f"{self.name}: asked for slot {position} but landed at {pulses} pulses "
                f"(slot {landed}); if this repeats, set slot_pulses in the config block"
            )
        self.log.info("%s: moved to position %d", self.name, position)

    def home(self) -> int | None:
        """Home the slider (slot 1); needed once after power-up."""
        pulses = self._finish_move(self._exchange("ho0"), "home")
        self.log.info("%s: homed (%d pulses)", self.name, pulses)
        return self._pulses_to_slot(pulses)

    def status_code(self) -> int:
        """The unit's GS status code (0 = ok; see STATUS_CODES)."""
        reply = self._exchange("gs")
        if reply[1:3] != "GS" or len(reply) < 5:
            raise ResponseError(f"{self.name}: bad status reply {reply!r}")
        return int(reply[3:5], 16)

    def status(self) -> dict:
        return {"position": self.position, "positions": self.POSITIONS}

    @classmethod
    def sim_responses(cls) -> dict:
        # travel 0x00A0 = 160 mm, 0x20 = 32 pulses/mm -> 1024 pulses/slot
        state = {"pulses": 0}

        def po() -> str:
            return f"0PO{state['pulses'] & 0xFFFFFFFF:08X}"

        def move(match: re.Match) -> str:
            state["pulses"] = int(match.group(1), 16)
            return po()

        def home(_match: re.Match) -> str:
            state["pulses"] = 0
            return po()

        return {
            "0in": "0IN0C112233442023010100A000000020",
            "0gs": "0GS00",
            "0gp": lambda _: po(),
            re.compile(r"0ma([0-9A-Fa-f]{8})$"): move,
            re.compile(r"0ho\d?$"): home,
        }
