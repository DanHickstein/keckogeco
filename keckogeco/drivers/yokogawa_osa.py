"""Yokogawa AQ63xx-series optical spectrum analyzer.

One command set covers the AQ6370C/D/E, AQ6373, AQ6375 and AQ6376 (the
"AQ6370 format"); ``_configure()`` sends ``CFORM1`` so a unit that powered
up in AQ6317 legacy-compatibility mode switches over before the first real
query. Wavelengths in nm, powers in dBm, matching the Agilent driver.

For now the unit hangs off the SECOND GPIB-USB adapter and is driven by
the standalone ``keckogeco/gui/yokogawa_app.py`` rather than through the
server. Keep it off board GPIB0 — that bus belongs to the server's Agilent
86142B, and two processes polling one board is the concurrent-GPIB pattern
that access-violates ni4882 (AGENTS.md). If the unit later joins the
server, put both OSAs behind the restored per-board VISA lock instead.
"""

from __future__ import annotations

from typing import ClassVar

import numpy as np

from .base import Instrument
from .errors import ResponseError
from .ieee_block import parse_float32_block

__all__ = ["YokogawaOSA"]

_TRACES = ("A", "B", "C", "D", "E", "F", "G")


class YokogawaOSA(Instrument):
    """AQ63xx OSA on GPIB (works unchanged over USB-TMC or TCPIP VISA)."""

    TRANSPORT_DEFAULTS: ClassVar[dict] = {
        "timeout_ms": 30_000,
        "read_termination": "\n",
        "write_termination": "\n",
    }

    #: common AQ63xx resolution bandwidths, best first. Each model offers a
    #: subset (e.g. the AQ6376 stops at 0.1 nm); an unsupported value is
    #: coerced by the instrument to the nearest legal one, so read back.
    RESOLUTIONS_NM: ClassVar[tuple[float, ...]] = (0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0)

    #: sensitivity modes in ``:SENS:SENS?`` reply-index order (0..6)
    SENSITIVITIES: ClassVar[tuple[str, ...]] = (
        "NHLD",  # normal hold
        "NAUT",  # normal auto
        "MID",
        "HIGH1",
        "HIGH2",
        "HIGH3",
        "NORMAL",
    )

    #: sweep modes in ``:INIT:SMOD?`` reply order (1..4)
    SWEEP_MODES: ClassVar[tuple[str, ...]] = ("SINGLE", "REPEAT", "AUTO", "SEGMENT")
    _SMOD_COMMANDS: ClassVar[dict[str, str]] = {
        "SINGLE": "SING",
        "REPEAT": "REP",
        "AUTO": "AUTO",
        "SEGMENT": "SEGM",
    }

    def __init__(self, transport, name: str = ""):
        super().__init__(transport, name)
        self._idn = ""

    def _configure(self) -> None:
        # CFORM1 is the AQ6317-legacy-mode command that switches the unit to
        # the AQ6370 command set; in the new mode it is accepted silently,
        # so sending it unconditionally makes the boot-time mode irrelevant.
        self.transport.write("CFORM1")
        idn = self.transport.query("*IDN?").strip()
        if "YOKOGAWA" not in idn.upper():
            raise ResponseError(f"{self.name}: unexpected *IDN? reply {idn!r}")
        self._idn = idn

    @property
    def identity(self) -> str:
        """The ``*IDN?`` string captured at connect (e.g. model + serial)."""
        return self._idn

    # ------------------------------------------------------------ settings

    @property
    def reference_level_dBm(self) -> float:
        return float(self.query(":DISP:TRAC:Y1:SCAL:RLEV?"))

    @reference_level_dBm.setter
    def reference_level_dBm(self, level: float) -> None:
        self.write(f":DISP:TRAC:Y1:SCAL:RLEV {float(level):.1f}DBM")

    @property
    def wl_start_nm(self) -> float:
        return float(self.query(":SENS:WAV:STAR?")) * 1e9

    @wl_start_nm.setter
    def wl_start_nm(self, nm: float) -> None:
        self.write(f":SENS:WAV:STAR {float(nm):.2f}NM")

    @property
    def wl_stop_nm(self) -> float:
        return float(self.query(":SENS:WAV:STOP?")) * 1e9

    @wl_stop_nm.setter
    def wl_stop_nm(self, nm: float) -> None:
        self.write(f":SENS:WAV:STOP {float(nm):.2f}NM")

    @property
    def wl_center_nm(self) -> float:
        return float(self.query(":SENS:WAV:CENT?")) * 1e9

    @wl_center_nm.setter
    def wl_center_nm(self, nm: float) -> None:
        self.write(f":SENS:WAV:CENT {float(nm):.2f}NM")

    @property
    def wl_span_nm(self) -> float:
        return float(self.query(":SENS:WAV:SPAN?")) * 1e9

    @wl_span_nm.setter
    def wl_span_nm(self, nm: float) -> None:
        self.write(f":SENS:WAV:SPAN {float(nm):.2f}NM")

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
        return float(self.query(":SENS:BAND:RES?")) * 1e9

    @resolution_nm.setter
    def resolution_nm(self, nm: float) -> None:
        self.write(f":SENS:BAND:RES {float(nm):g}NM")

    @property
    def sensitivity(self) -> str:
        """Sensitivity mode name (the AQ63xx enumeration, not a dBm level)."""
        reply = self.query(":SENS:SENS?")
        try:
            return self.SENSITIVITIES[int(float(reply))]
        except (ValueError, IndexError) as exc:
            raise ResponseError(f"{self.name}: unexpected sensitivity reply {reply!r}") from exc

    @sensitivity.setter
    def sensitivity(self, mode: str) -> None:
        mode = str(mode).strip().upper()
        mode = {"NORM": "NORMAL", "NORMAL/HOLD": "NHLD", "NORMAL/AUTO": "NAUT"}.get(mode, mode)
        if mode not in self.SENSITIVITIES:
            raise ValueError(f"sensitivity must be one of {self.SENSITIVITIES}, got {mode!r}")
        self.write(f":SENS:SENS {mode}")

    # --------------------------------------------------------------- sweep

    @property
    def sweep_mode(self) -> str:
        reply = self.query(":INIT:SMOD?")
        try:
            return self.SWEEP_MODES[int(float(reply)) - 1]
        except (ValueError, IndexError) as exc:
            raise ResponseError(f"{self.name}: unexpected sweep-mode reply {reply!r}") from exc

    @sweep_mode.setter
    def sweep_mode(self, mode: str) -> None:
        command = self._SMOD_COMMANDS.get(str(mode).strip().upper())
        if command is None:
            raise ValueError(f"sweep mode must be one of {self.SWEEP_MODES}, got {mode!r}")
        self.write(f":INIT:SMOD {command}")

    def sweep(self, mode: str | None = None) -> None:
        """Start a sweep, optionally selecting SINGLE/REPEAT/AUTO first."""
        with self.lock:
            if mode is not None:
                self.sweep_mode = mode
            self.write(":INIT")

    def abort(self) -> None:
        """Stop the current sweep (front panel's STOP key)."""
        self.write(":ABOR")

    # -------------------------------------------------------------- traces

    #: one GPIB read covers the whole trace message (the read ends at EOI,
    #: not at this count): block header + 4 bytes/point + terminator
    _TRACE_READ_MAX = 2 + 10 + 4 * 100_001 + 2

    def get_spectrum(self, trace: str = "A") -> tuple[np.ndarray, np.ndarray]:
        """(wavelength_nm, power_dBm) for one trace, via a REAL,32 binary
        block (same rationale as the Agilent driver: ASCII pulls hold the
        bus long enough to visibly stall the sweep)."""
        trace = str(trace).upper()
        if trace not in _TRACES:
            raise ValueError(f"trace must be one of {_TRACES}, got {trace!r}")
        with self.lock:  # keep format-set, query, and block read adjacent
            self.write(":FORM:DATA REAL,32")
            self.write(f":TRAC:Y? TR{trace}")
            raw = self.read_bytes(self._TRACE_READ_MAX)
            # the AQ6376 sends REAL,32 little-endian (rack-verified 07-21)
            power = parse_float32_block(raw, self.name, byteorder="<")
            wavelength = np.linspace(self.wl_start_nm, self.wl_stop_nm, len(power))
        return wavelength, power

    def status(self) -> dict:
        return {
            "wl_start_nm": self.wl_start_nm,
            "wl_stop_nm": self.wl_stop_nm,
            "resolution_nm": self.resolution_nm,
            "sensitivity": self.sensitivity,
            "sweep_mode": self.sweep_mode,
            "reference_level_dBm": self.reference_level_dBm,
        }

    # ----------------------------------------------------------------- sim

    @classmethod
    def sim_responses(cls) -> dict:
        import re

        state = {
            "start_m": 1.540e-6,
            "stop_m": 1.580e-6,
            "rlev": "-20.0",
            "res_m": 1e-10,  # 0.1 nm
            "sens": "3",  # HIGH1
            "smod": "2",  # REPEAT
        }

        def trace(_):
            # gaussian envelope with comb teeth, peaked at the window center
            # so the sim looks alive at any range; framed as the REAL,32
            # block the driver reads (latin-1 keeps byte values intact)
            n = 1001
            wl = np.linspace(state["start_m"], state["stop_m"], n) * 1e9
            center = (wl[0] + wl[-1]) / 2
            width = max((wl[-1] - wl[0]) / 5, 1e-6)
            envelope = -25 + 20 * np.exp(-(((wl - center) / width) ** 2))
            teeth = 3 * (np.cos(2 * np.pi * (wl - center) / 0.13) > 0.6)
            payload = (envelope + teeth - 3).astype("<f4").tobytes()
            header = f"#{len(str(len(payload)))}{len(payload)}".encode("ascii")
            return (header + payload).decode("latin-1")

        def set_wl(key):
            def _set(m):
                state[key] = float(m.group(1)) * 1e-9
                return ""

            return _set

        def set_center(m):
            span = state["stop_m"] - state["start_m"]
            center = float(m.group(1)) * 1e-9
            state["start_m"] = center - span / 2
            state["stop_m"] = center + span / 2
            return ""

        def set_span(m):
            center = (state["start_m"] + state["stop_m"]) / 2
            span = float(m.group(1)) * 1e-9
            state["start_m"] = center - span / 2
            state["stop_m"] = center + span / 2
            return ""

        def set_res(m):
            state["res_m"] = float(m.group(1)) * 1e-9
            return ""

        def set_sens(m):
            state["sens"] = str(cls.SENSITIVITIES.index(m.group(1)))
            return ""

        def set_smod(m):
            state["smod"] = str([v for v in cls._SMOD_COMMANDS.values()].index(m.group(1)) + 1)
            return ""

        def set_rlev(m):
            state["rlev"] = m.group(1)
            return ""

        return {
            "*IDN?": "YOKOGAWA,AQ6376,SIM000001,01.00",
            "CFORM1": "",
            ":SENS:WAV:STAR?": lambda _: f"{state['start_m']:.6e}",
            ":SENS:WAV:STOP?": lambda _: f"{state['stop_m']:.6e}",
            ":SENS:WAV:CENT?": lambda _: f"{(state['start_m'] + state['stop_m']) / 2:.6e}",
            ":SENS:WAV:SPAN?": lambda _: f"{state['stop_m'] - state['start_m']:.6e}",
            re.compile(r":SENS:WAV:STAR ([\d.]+)NM$"): set_wl("start_m"),
            re.compile(r":SENS:WAV:STOP ([\d.]+)NM$"): set_wl("stop_m"),
            re.compile(r":SENS:WAV:CENT ([\d.]+)NM$"): set_center,
            re.compile(r":SENS:WAV:SPAN ([\d.]+)NM$"): set_span,
            ":SENS:BAND:RES?": lambda _: f"{state['res_m']:.3e}",
            re.compile(r":SENS:BAND:RES ([\d.]+)NM$"): set_res,
            ":SENS:SENS?": lambda _: state["sens"],
            re.compile(r":SENS:SENS (\w+)$"): set_sens,
            ":INIT:SMOD?": lambda _: state["smod"],
            re.compile(r":INIT:SMOD (\w+)$"): set_smod,
            ":INIT": "",
            ":ABOR": "",
            ":DISP:TRAC:Y1:SCAL:RLEV?": lambda _: state["rlev"],
            re.compile(r":DISP:TRAC:Y1:SCAL:RLEV (-?[\d.]+)DBM$"): set_rlev,
            re.compile(r":TRAC:Y\? TR\w$"): trace,
            ":FORM:DATA REAL,32": "",
        }
