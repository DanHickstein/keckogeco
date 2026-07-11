"""SRS FS725 rubidium frequency standard.

Read-only health monitor: the FS725 is the 10 MHz reference for the RF
chain, and the comb only needs to know that it is phase- and
frequency-locked. Ported from ``Hardware/RbClock.py``.
"""

from __future__ import annotations

from typing import ClassVar

from .base import Instrument
from .errors import ResponseError

__all__ = ["RbClock"]


class RbClock(Instrument):
    """SRS FS725 on a VISA serial port (9600 baud, CR terminations)."""

    TRANSPORT_DEFAULTS: ClassVar[dict] = {
        "read_termination": "\r",
        "write_termination": "\r",
    }

    SIM_RESPONSES: ClassVar[dict] = {
        "SN?": "12345",
        "PL?": "1",
        "LO?": "1",
    }

    @property
    def serial_number(self) -> str:
        return self.query("SN?")

    def _locked(self, cmd: str, what: str) -> bool:
        reply = self.query(cmd)
        if reply not in ("0", "1"):
            raise ResponseError(f"{self.name}: bad {what} reply {reply!r}")
        return reply == "1"

    @property
    def phase_locked(self) -> bool:
        """True when the 10 MHz output is phase-locked to the Rb transition."""
        return self._locked("PL?", "phase-lock")

    @property
    def frequency_locked(self) -> bool:
        """True when the frequency lock loop is closed."""
        return self._locked("LO?", "frequency-lock")

    def status(self) -> dict:
        return {
            "phase_locked": self.phase_locked,
            "frequency_locked": self.frequency_locked,
        }
