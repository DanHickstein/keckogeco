"""Agilent 86142B optical spectrum analyzer.

The main diagnostic for the minicomb and flattener spectra. Ported from
``Hardware/Agilent_86142B.py`` without the matplotlib plotting — the
driver returns arrays and the GUIs/web page do the drawing.
"""

from __future__ import annotations

from typing import ClassVar

import numpy as np

from .base import Instrument
from .errors import ResponseError

__all__ = ["Agilent86142B"]

_TRACES = ("A", "B", "C", "D", "E", "F")


class Agilent86142B(Instrument):
    """86142B OSA on GPIB. Wavelengths in nm, powers in dBm."""

    TRANSPORT_DEFAULTS: ClassVar[dict] = {
        "timeout_ms": 25_000,
        "read_termination": "\n",
        "write_termination": "\r\n",
    }

    # ------------------------------------------------------------ settings

    @property
    def reference_level_dBm(self) -> float:
        return float(self.query("DISP:WIND:TRAC:Y:SCAL:RLEV?"))

    @reference_level_dBm.setter
    def reference_level_dBm(self, level: float) -> None:
        self.write(f"DISP:WIND:TRAC:Y:SCAL:RLEV {float(level):.1f}")

    @property
    def wl_start_nm(self) -> float:
        return float(self.query("SENS:WAV:STAR?")) * 1e9

    @wl_start_nm.setter
    def wl_start_nm(self, nm: float) -> None:
        self.write(f"SENS:WAV:STAR {float(nm):.2f}nm")

    @property
    def wl_stop_nm(self) -> float:
        return float(self.query("SENS:WAV:STOP?")) * 1e9

    @wl_stop_nm.setter
    def wl_stop_nm(self, nm: float) -> None:
        self.write(f"SENS:WAV:STOP {float(nm):.2f}nm")

    @property
    def wl_center_nm(self) -> float:
        return (self.wl_start_nm + self.wl_stop_nm) / 2

    @wl_center_nm.setter
    def wl_center_nm(self, nm: float) -> None:
        span = self.wl_span_nm
        self.wl_start_nm = nm - span / 2
        self.wl_stop_nm = nm + span / 2

    @property
    def wl_span_nm(self) -> float:
        return self.wl_stop_nm - self.wl_start_nm

    @wl_span_nm.setter
    def wl_span_nm(self, nm: float) -> None:
        center = self.wl_center_nm
        self.wl_start_nm = center - nm / 2
        self.wl_stop_nm = center + nm / 2

    # -------------------------------------------------------------- traces

    def get_spectrum(self, trace: str = "A") -> tuple[np.ndarray, np.ndarray]:
        """(wavelength_nm, power_dBm) for one trace."""
        trace = str(trace).upper()
        if trace not in _TRACES:
            raise ValueError(f"trace must be one of {_TRACES}, got {trace!r}")
        self.write("form ascii")
        raw = self.query(f"trac:data:y? tr{trace}").replace("\n", "")
        try:
            power = np.array(raw.split(","), dtype=float)
        except ValueError as exc:
            raise ResponseError(f"{self.name}: unparseable trace data {raw[:80]!r}") from exc
        wavelength = np.linspace(self.wl_start_nm, self.wl_stop_nm, len(power))
        return wavelength, power

    def status(self) -> dict:
        return {
            "wl_start_nm": self.wl_start_nm,
            "wl_stop_nm": self.wl_stop_nm,
            "reference_level_dBm": self.reference_level_dBm,
        }

    # ----------------------------------------------------------------- sim

    @classmethod
    def sim_responses(cls) -> dict:
        import re

        state = {"start_m": 1.545e-6, "stop_m": 1.575e-6, "rlev": "-10.0"}

        def trace(_):
            # a comb-ish spectrum: gaussian envelope with 0.13 nm teeth
            n = 501
            wl = np.linspace(state["start_m"], state["stop_m"], n) * 1e9
            envelope = -20 + 15 * np.exp(-(((wl - 1560) / 6) ** 2))
            teeth = 3 * (np.cos(2 * np.pi * (wl - 1560) / 0.13) > 0.6)
            return ",".join(f"{v:.2f}" for v in envelope + teeth - 3)

        def set_wl(key):
            def _set(m):
                state[key] = float(m.group(1)) * 1e-9
                return ""

            return _set

        return {
            "SENS:WAV:STAR?": lambda _: f"{state['start_m']:.3e}",
            "SENS:WAV:STOP?": lambda _: f"{state['stop_m']:.3e}",
            re.compile(r"SENS:WAV:STAR ([\d.]+)nm$"): set_wl("start_m"),
            re.compile(r"SENS:WAV:STOP ([\d.]+)nm$"): set_wl("stop_m"),
            "DISP:WIND:TRAC:Y:SCAL:RLEV?": lambda _: state["rlev"],
            re.compile(r"trac:data:y\? tr\w$"): trace,
            "form ascii": "",
        }
