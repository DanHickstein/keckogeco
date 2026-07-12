"""Wavelength References Clarity laser (Rb-referenced lock).

SCPI-ish text protocol on a VISA serial port. Note the asymmetric
terminations (reads end ``\\r\\n``, writes end ``\\n``) — communication
fails without them (rack-verified in the old driver,
``Hardware/Clarity.py``).

The KTL keyword ``LFC_CLARITY_ONOFF`` collapses the four-state status
(0 off / 1 calibrating / 2 locking / 3 locked) to off/on the same way
the old orchestration did.
"""

from __future__ import annotations

import re
from typing import ClassVar

from .base import Instrument
from .errors import ResponseError
from .transports import Transport

__all__ = ["Clarity"]


class Clarity(Instrument):
    """Clarity laser: output on/off, lock status, internal calibration."""

    TRANSPORT_DEFAULTS: ClassVar[dict] = {
        "timeout_ms": 5_000,
        "baud_rate": 9_600,
        "read_termination": "\r\n",
        "write_termination": "\n",
    }

    STATUS_NAMES: ClassVar[dict[int, str]] = {
        0: "off",
        1: "calibrating",
        2: "locking",
        3: "locked",
    }

    def __init__(self, transport: Transport, name: str = ""):
        super().__init__(transport, name)

    def _query(self, cmd: str) -> str:
        return self._io(lambda: self.transport.query(cmd)).strip()

    def _write(self, cmd: str) -> None:
        self._io(lambda: self.transport.write(cmd))

    @property
    def status_code(self) -> int:
        """0 = off, 1 = calibrating, 2 = locking, 3 = locked."""
        reply = self._query("SYST:STAT?")
        match = re.search(r"[0-3]\s*$", reply)
        if match is None:
            raise ResponseError(f"{self.name}: bad SYST:STAT? reply {reply!r}")
        return int(match.group(0))

    @property
    def status_name(self) -> str:
        return self.STATUS_NAMES[self.status_code]

    @property
    def output(self) -> bool:
        """Laser output state (``SOUR:STAT?`` -> ON/OFF)."""
        reply = self._query("SOUR:STAT?")
        if reply.upper().endswith("ON"):
            return True
        if reply.upper().endswith("OFF"):
            return False
        raise ResponseError(f"{self.name}: bad SOUR:STAT? reply {reply!r}")

    def set_output(self, on: bool) -> None:
        self._write(f"SOUR:STAT {1 if on else 0}")
        self.log.info("%s: output -> %s", self.name, "ON" if on else "OFF")

    def auto_on(self) -> None:
        """Run the internal calibration sequence (``CAL:INT``)."""
        self._write("CAL:INT")
        self.log.info("%s: internal calibration started", self.name)

    def status(self) -> dict:
        return {"status": self.status_name, "output": self.output}

    @classmethod
    def sim_responses(cls) -> dict:
        state = {"on": False}

        def set_output(m):
            state["on"] = m.group(1) == "1"
            return ""

        return {
            "SYST:STAT?": lambda _: "3" if state["on"] else "0",
            "SOUR:STAT?": lambda _: "ON" if state["on"] else "OFF",
            re.compile(r"SOUR:STAT ([01])$"): set_output,
            "CAL:INT": "",
        }
