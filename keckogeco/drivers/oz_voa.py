"""OZ Optics variable optical attenuator.

Three units on the rack (1310 / 1550 / 2000 nm). Protocol: text commands,
multi-line replies terminated by a ``Done`` line. Ported from
``Hardware/OZopticsVOA.py``.
"""

from __future__ import annotations

import math
import re
import time
from typing import ClassVar

from .base import Instrument
from .errors import ResponseError
from .transports import SimTransport, Transport

__all__ = ["OZOpticsVOA"]


class OZOpticsVOA(Instrument):
    """OZ Optics VOA on a VISA serial port. Attenuation in dB."""

    TRANSPORT_DEFAULTS: ClassVar[dict] = {
        "timeout_ms": 25_000,
        "baud_rate": 9_600,
        "read_termination": "\r\n",
        "write_termination": "\r\n",
    }

    MAX_REPLY_LINES: ClassVar[int] = 32
    #: the unit needs a beat between commands
    COMMAND_INTERVAL_S: ClassVar[float] = 0.1
    SIM_DEFAULT: ClassVar[str] = "Done"  # every reply stream ends with Done

    def __init__(self, transport: Transport, name: str = ""):
        super().__init__(transport, name)
        self._sim = isinstance(transport, SimTransport)

    def _ask(self, cmd: str) -> str:
        """Send a command; collect reply lines until 'Done'."""

        def op() -> str:
            if not self._sim:
                time.sleep(self.COMMAND_INTERVAL_S)
            self.transport.clear()
            self.transport.write(cmd)
            lines: list[str] = []
            for _ in range(self.MAX_REPLY_LINES):
                line = self.transport.read()
                if line.strip() == "Done":
                    return "\n".join(lines)
                if line.strip().startswith("Error"):
                    raise ResponseError(f"{self.name}: {cmd!r} -> {line.strip()!r}")
                lines.append(line)
            raise ResponseError(
                f"{self.name}: no 'Done' after {self.MAX_REPLY_LINES} lines for {cmd!r}"
            )

        return self._io(op)

    @property
    def attenuation_dB(self) -> float:
        """Current attenuation; NaN if the unit hasn't homed yet.

        After a power cycle the unit answers ``Atten:unknown`` until the
        first attenuation set (rack observation, 2026-07-12) — that's a
        real state, not an error.
        """
        reply = self._ask("A?")  # e.g. 'Atten:12.00(dB)' or 'Atten:unknown'
        if "unknown" in reply.casefold():
            self.log.warning(
                "%s: attenuation unknown (not homed since power-up); set an "
                "attenuation once to initialize it",
                self.name,
            )
            return math.nan
        match = re.search(r"Atten\s*:\s*([\d.]+)", reply, re.IGNORECASE)
        if match is None:
            raise ResponseError(f"{self.name}: bad attenuation reply {reply!r}")
        return float(match.group(1))

    @attenuation_dB.setter
    def attenuation_dB(self, dB: float) -> None:
        self._ask(f"A{float(dB):.2f}")
        self.log.info("%s: attenuation -> %.2f dB", self.name, dB)

    def config_text(self) -> str:
        """The unit's CD configuration dump (wavelength, serial, limits)."""
        return self._ask("CD")

    def status(self) -> dict:
        return {"attenuation_dB": self.attenuation_dB}

    @classmethod
    def sim_responses(cls) -> dict:
        state = {"atten": 0.0}

        def set_atten(m):
            state["atten"] = float(m.group(1))
            return "Done"

        # The driver reads lines until 'Done'; SIM_DEFAULT supplies the
        # trailing Done for payload replies.
        return {
            "A?": lambda _: f"Atten:{state['atten']:.2f}(dB)",
            re.compile(r"A([\d.]+)$"): set_atten,
            "CD": "DD100MC",
        }
