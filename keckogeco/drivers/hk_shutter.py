"""HK optical shutter (Thorlabs SC10-style serial console).

The controller echoes every character and terminates replies with a
``>`` prompt, so queries drain until the prompt and strip the echo —
replacing the old driver's fragile byte-count arithmetic
(``Hardware/hk_shutter.py``).

Commands: ``ens?`` reads the enable state (1 = open), ``ens`` toggles it,
``mode?``/``mode=N`` reads/sets the operating mode (1 = manual).
"""

from __future__ import annotations

import re
import time
from typing import ClassVar

from .base import Instrument
from .errors import ResponseError
from .transports import SimTransport, Transport

__all__ = ["HKShutter"]


class HKShutter(Instrument):
    """SC10-style shutter on a plain COM port."""

    DEFAULT_TRANSPORT: ClassVar[str] = "serial"
    TRANSPORT_DEFAULTS: ClassVar[dict] = {
        "baud_rate": 9_600,
        "timeout_s": 1.0,
        "terminator": "\r",
    }
    RESPONSE_DELAY_S: ClassVar[float] = 0.2

    def __init__(self, transport: Transport, name: str = ""):
        super().__init__(transport, name)
        self._sim = isinstance(transport, SimTransport)

    def _ask(self, cmd: str) -> str:
        """Send a command; return the reply with echo and prompt stripped."""

        def op() -> str:
            self.transport.clear()
            self.transport.write(cmd)
            if not self._sim:
                time.sleep(self.RESPONSE_DELAY_S)
            raw = self.transport.read_available().decode("ascii", "replace")
            # typical raw: 'ens?\r1\r> '  -> echo, value, prompt
            text = raw.replace(">", " ").strip()
            if text.startswith(cmd):
                text = text[len(cmd) :]
            return text.strip()

        return self._io(op)

    @property
    def open(self) -> bool:
        reply = self._ask("ens?")
        match = re.search(r"[01]", reply)
        if match is None:
            raise ResponseError(f"{self.name}: bad ens? reply {reply!r}")
        return match.group(0) == "1"

    def set_open(self, want_open: bool) -> None:
        """Toggle only if the state differs (the command is a toggle)."""
        if self.open != bool(want_open):
            self._ask("ens")
            self.log.info("%s: shutter -> %s", self.name, "OPEN" if want_open else "CLOSED")

    @property
    def mode(self) -> int:
        reply = self._ask("mode?")
        match = re.search(r"\d", reply)
        if match is None:
            raise ResponseError(f"{self.name}: bad mode? reply {reply!r}")
        return int(match.group(0))

    def set_mode(self, mode: int) -> None:
        self._ask(f"mode={int(mode)}")

    def status(self) -> dict:
        return {"open": self.open, "mode": self.mode}

    @classmethod
    def sim_responses(cls) -> dict:
        state = {"ens": "0", "mode": "1"}

        def toggle(_):
            state["ens"] = "0" if state["ens"] == "1" else "1"
            return "> "

        def set_mode(m):
            state["mode"] = m.group(1)
            return "> "

        return {
            "ens?": lambda _: f"{state['ens']}\r> ",
            "ens": toggle,
            "mode?": lambda _: f"{state['mode']}\r> ",
            re.compile(r"mode=(\d)$"): set_mode,
        }
