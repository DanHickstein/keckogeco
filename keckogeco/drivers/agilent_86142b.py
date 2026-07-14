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

    #: the 86142B's selectable resolution bandwidths, best first
    RESOLUTIONS_NM: ClassVar[tuple[float, ...]] = (0.06, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0)

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

    def set_range(self, start_nm: float | None = None, stop_nm: float | None = None) -> None:
        """Set start/stop together, ordering the writes so the pair never
        passes through an inverted (start > stop) state on the instrument."""
        if start_nm is not None and stop_nm is not None:
            if float(start_nm) <= self.wl_stop_nm:
                self.wl_start_nm = start_nm
                self.wl_stop_nm = stop_nm
            else:
                self.wl_stop_nm = stop_nm
                self.wl_start_nm = start_nm
        elif start_nm is not None:
            self.wl_start_nm = start_nm
        elif stop_nm is not None:
            self.wl_stop_nm = stop_nm

    @property
    def resolution_nm(self) -> float:
        """Resolution bandwidth in nm (0.06 is the best this OSA offers)."""
        return float(self.query("SENS:BAND:RES?")) * 1e9

    @resolution_nm.setter
    def resolution_nm(self, nm: float) -> None:
        self.write(f"SENS:BAND:RES {float(nm):g}nm")

    @property
    def sensitivity_dBm(self) -> float:
        """Measurement sensitivity: on the 86142B this is a level in dBm
        (not the norm/high1-3 enumeration of other OSA families)."""
        return float(self.query("SENS:POW:DC:RANG:LOW?"))

    @sensitivity_dBm.setter
    def sensitivity_dBm(self, dBm: float) -> None:
        self.write(f"SENS:POW:DC:RANG:LOW {float(dBm):.1f}")

    # --------------------------------------------------------------- sweep

    @property
    def sweep_continuous(self) -> bool:
        return bool(int(self.query("INIT:CONT?")))

    @sweep_continuous.setter
    def sweep_continuous(self, on: bool) -> None:
        self.write(f"INIT:CONT {1 if on else 0}")

    def trigger_single(self) -> None:
        """One sweep, then hold (the front panel's Single softkey)."""
        self.write("INIT:CONT 0")
        self.write("INIT:IMM")

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
            "resolution_nm": self.resolution_nm,
            "sensitivity_dBm": self.sensitivity_dBm,
            "sweep_continuous": self.sweep_continuous,
            "reference_level_dBm": self.reference_level_dBm,
        }

    # ----------------------------------------------------------------- sim

    @classmethod
    def sim_responses(cls) -> dict:
        import re

        state = {
            "start_m": 1.545e-6,
            "stop_m": 1.575e-6,
            "rlev": "-10.0",
            "res_m": 6e-11,  # 0.06 nm
            "sens": "-60.0",
            "cont": "1",
        }

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

        def set_state(key):
            def _set(m):
                state[key] = m.group(1)
                return ""

            return _set

        def set_res(m):
            state["res_m"] = float(m.group(1)) * 1e-9
            return ""

        return {
            "SENS:WAV:STAR?": lambda _: f"{state['start_m']:.3e}",
            "SENS:WAV:STOP?": lambda _: f"{state['stop_m']:.3e}",
            re.compile(r"SENS:WAV:STAR ([\d.]+)nm$"): set_wl("start_m"),
            re.compile(r"SENS:WAV:STOP ([\d.]+)nm$"): set_wl("stop_m"),
            "SENS:BAND:RES?": lambda _: f"{state['res_m']:.3e}",
            re.compile(r"SENS:BAND:RES ([\d.]+)nm$"): set_res,
            "SENS:POW:DC:RANG:LOW?": lambda _: state["sens"],
            re.compile(r"SENS:POW:DC:RANG:LOW (-?[\d.]+)$"): set_state("sens"),
            "INIT:CONT?": lambda _: state["cont"],
            re.compile(r"INIT:CONT ([01])$"): set_state("cont"),
            "INIT:IMM": "",
            "DISP:WIND:TRAC:Y:SCAL:RLEV?": lambda _: state["rlev"],
            re.compile(r"trac:data:y\? tr\w$"): trace,
            "form ascii": "",
        }
