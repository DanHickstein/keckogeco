"""Tektronix TDS 2024C oscilloscope (USB-TMC).

Diagnostics only (tier 3): the scope watches the autocorrelator and RF
monitor points. Ported from ``Hardware/TDS2024C.py`` without the
matplotlib plotting and file saving — the driver returns arrays and the
GUIs do the drawing. The Lorentzian autocorrelation fit stayed behind
too; that is analysis, not instrument I/O.

Protocol notes (rack-verified in the old driver): reads end ``\\n``,
writes end ``\\r\\n``, and a full 2500-point ASCII curve transfer takes
seconds, hence the long timeout.
"""

from __future__ import annotations

from typing import ClassVar

import numpy as np

from .base import Instrument
from .errors import ResponseError

__all__ = ["TDS2024C"]

CHANNELS = (1, 2, 3, 4)

#: WFMPre? preamble fields, in reply order (semicolon-separated).
_PREAMBLE_FIELDS = (
    "BYT_Nr",
    "BIT_Nr",
    "ENCdg",
    "BN_Fmt",
    "BYT_Or",
    "NR_Pt",
    "WFID",
    "PT_FMT",
    "XINcr",
    "PT_Off",
    "XZEro",
    "XUNit",
    "YMUlt",
    "YZEro",
    "YOFF",
    "YUNit",
)


def _check_channel(channel: int) -> int:
    if channel not in CHANNELS:
        raise ValueError(f"channel must be one of {CHANNELS}, got {channel!r}")
    return channel


class TDS2024C(Instrument):
    """TDS 2024C four-channel scope. Times in s, voltages in V."""

    TRANSPORT_DEFAULTS: ClassVar[dict] = {
        "timeout_ms": 25_000,
        "read_termination": "\n",
        "write_termination": "\r\n",
    }

    # ------------------------------------------------------------- vertical

    def vertical_scale_V(self, channel: int) -> float:
        """Volts per division for one channel."""
        return float(self.query(f"CH{_check_channel(channel)}:SCA?"))

    def set_vertical_scale_V(self, channel: int, volts_per_div: float) -> None:
        self.write(f"CH{_check_channel(channel)}:SCA {float(volts_per_div):g}")

    def vertical_position_div(self, channel: int) -> float:
        return float(self.query(f"CH{_check_channel(channel)}:POS?"))

    def set_vertical_position_div(self, channel: int, divisions: float) -> None:
        self.write(f"CH{_check_channel(channel)}:POS {float(divisions):g}")

    def display(self, channel: int) -> bool:
        return self.query(f"SEL:CH{_check_channel(channel)}?").strip() in ("1", "ON")

    def set_display(self, channel: int, on: bool) -> None:
        self.write(f"SEL:CH{_check_channel(channel)} {'ON' if on else 'OFF'}")

    # ----------------------------------------------------------- horizontal

    @property
    def time_scale_s(self) -> float:
        """Seconds per division."""
        return float(self.query("HOR:SCA?"))

    @time_scale_s.setter
    def time_scale_s(self, seconds_per_div: float) -> None:
        self.write(f"HOR:SCA {float(seconds_per_div):g}")

    @property
    def time_position_s(self) -> float:
        return float(self.query("HOR:POS?"))

    @time_position_s.setter
    def time_position_s(self, seconds: float) -> None:
        self.write(f"HOR:POS {float(seconds):g}")

    # -------------------------------------------------------------- trigger

    def set_trigger_source(self, channel: int) -> None:
        self.write(f"TRIG:MAI:EDGE:SOU CH{_check_channel(channel)}")

    def set_trigger_level_V(self, level: float) -> None:
        self.write(f"TRIG:MAI:LEV {float(level):g}")

    # --------------------------------------------------------------- traces

    def get_trace(self, channel: int = 2) -> tuple[np.ndarray, np.ndarray]:
        """(time_s, voltage_V) for one channel, full 2500-point record."""
        self.write(f"DAT:SOU CH{_check_channel(channel)}")
        self.write("DAT:ENC ASCI")
        self.write("WFMPre:PT_Fmt Y")
        self.write("DAT:WID 2")
        self.write("DAT:STAR 1")
        self.write("DAT:STOP 2500")

        preamble = self.preamble()
        raw = self.query("CURV?")
        try:
            curve = np.array(raw.split(","), dtype=float)
        except ValueError as exc:
            raise ResponseError(f"{self.name}: unparseable CURV? data {raw[:80]!r}") from exc
        n = int(preamble["NR_Pt"])
        if len(curve) != n:
            raise ResponseError(f"{self.name}: expected {n} points, got {len(curve)}")

        voltage = float(preamble["YZEro"]) + float(preamble["YMUlt"]) * (
            curve - float(preamble["YOFF"])
        )
        time = float(preamble["XZEro"]) + float(preamble["XINcr"]) * (
            np.arange(n) - float(preamble["PT_Off"])
        )
        return time, voltage

    def preamble(self) -> dict:
        """The WFMPre? waveform preamble as a field dict (strings)."""
        reply = self.query("WFMP?")
        parts = reply.split(";")
        if len(parts) < len(_PREAMBLE_FIELDS):
            raise ResponseError(f"{self.name}: short WFMP? reply {reply[:80]!r}")
        return dict(zip(_PREAMBLE_FIELDS, parts, strict=False))

    def status(self) -> dict:
        return {
            "time_scale_s": self.time_scale_s,
            "displayed": [ch for ch in CHANNELS if self.display(ch)],
        }

    # ------------------------------------------------------------------ sim

    @classmethod
    def sim_responses(cls) -> dict:
        import re

        state = {"time_scale": "2.5e-4", "displayed": {1: False, 2: True, 3: False, 4: False}}
        n = 2500
        # preamble matching the synthetic curve below: 16-bit signed counts,
        # 25 mV/count, centered record
        pre = f'2;16;ASC;RI;MSB;{n};"sim";Y;1.0e-6;{n / 2};0.0;"s";2.5e-2;0.0;0.0;"V"'

        def curve(_):
            # an autocorrelation-ish peak in raw ADC counts
            x = np.arange(n) - n / 2
            counts = np.round(100 / (1 + (x / 40.0) ** 2)).astype(int)
            return ",".join(str(c) for c in counts)

        def set_display(m):
            state["displayed"][int(m.group(1))] = m.group(2) == "ON"
            return ""

        def set_time_scale(m):
            state["time_scale"] = m.group(1)
            return ""

        return {
            "WFMP?": pre,
            "CURV?": curve,
            "HOR:SCA?": lambda _: state["time_scale"],
            re.compile(r"HOR:SCA (\S+)$"): set_time_scale,
            "HOR:POS?": "0.0",
            re.compile(r"SEL:CH([1-4])\?$"): lambda m: (
                "1" if state["displayed"][int(m.group(1))] else "0"
            ),
            re.compile(r"SEL:CH([1-4]) (ON|OFF)$"): set_display,
            re.compile(r"CH[1-4]:SCA\?$"): "1.0",
            re.compile(r"CH[1-4]:POS\?$"): "0.0",
        }
