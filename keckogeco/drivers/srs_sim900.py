"""SRS SIM900 mainframe with SIM960 PID and SIM928 voltage-source modules.

The SIM900 is a chassis: talking to a module means connecting the
mainframe's serial stream to a slot (``CONN <slot>, "<esc>"``) and
escaping back with a VISA device-clear. Ported from
``Hardware/SRS_SIM900.py``. Behaviors preserved:

* every slot operation clears first, then reconnects (the old driver
  stopped trusting cached slot state — "Don't check anymore, clear device
  anyway");
* one automatic retry after a 1 s pause on VISA I/O errors (the mainframe
  occasionally drops a transaction) — provided by the base class;
* SIM960 numeric settings validate against the module's documented ranges
  and resolutions before writing;
* SIM960 manual-output changes can ramp in small voltage steps
  (``manual_output_ramp``), used when the IM bias lock hands over.

The SIM960 modules run the intensity-modulator bias lock and the filter
cavity locks; the SIM928 provides an isolated bias voltage.
"""

from __future__ import annotations

from math import log10
from typing import ClassVar

import numpy as np

from .base import Instrument
from .errors import ResponseError

__all__ = ["SIM900", "SIM928", "SIM960"]

_ESCAPE = "xyx"  # escape string armed on every CONN


def _as_onoff(value) -> str:
    text = str(value).strip().casefold()
    if text in ("1", "on", "true"):
        return "1"
    if text in ("0", "off", "false"):
        return "0"
    raise ValueError(f"Expected 0/1/'on'/'off', got {value!r}")


class SIM900(Instrument):
    """SIM900 mainframe. Module wrappers are created via :meth:`sim960` /
    :meth:`sim928` (or directly with the module classes)."""

    TRANSPORT_DEFAULTS: ClassVar[dict] = {
        "timeout_ms": 25_000,
        "baud_rate": 9_600,
        "read_termination": "\r\n",
        "write_termination": "\r\n",
    }

    def clear(self) -> None:
        """Escape any module connection; mainframe becomes active."""
        with self.lock:
            self.transport.clear()

    def _connect_slot(self, slot: int) -> None:
        self.transport.write(f'CONN {slot}, "{_ESCAPE}"')

    def query_slot(self, slot: int, cmd: str) -> str:
        """Route one query to a module slot (clear -> CONN -> query)."""

        def op() -> str:
            self.transport.clear()
            self._connect_slot(slot)
            return self.transport.query(cmd)

        return self._io(op)

    def write_slot(self, slot: int, cmd: str) -> None:
        def op() -> None:
            self.transport.clear()
            self._connect_slot(slot)
            self.transport.write(cmd)

        self._io(op)

    def sim960(self, slot: int, name: str = "") -> SIM960:
        return SIM960(self, slot, name)

    def sim928(self, slot: int, name: str = "") -> SIM928:
        return SIM928(self, slot, name)

    # ----------------------------------------------------------------- sim

    @classmethod
    def sim_responses(cls) -> dict:
        import math
        import re

        # Per-slot module state; CONN selects the active slot. Defaults are
        # a SIM960 in any slot (the SIM928 commands don't collide).
        frames = {"slot": 0}
        modules: dict[int, dict] = {}

        def module():
            return modules.setdefault(
                frames["slot"],
                {
                    "PCTL": "0",
                    "ICTL": "0",
                    "DCTL": "0",
                    "OCTL": "0",
                    "GAIN": "-1.0e-1",
                    "INTG": "5.0e2",
                    "DERV": "1.0e-5",
                    "OFST": "0.0",
                    "AMAN": "0",
                    "INPT": "0",
                    "SETP": "0.0",
                    "RAMP": "0",
                    "RATE": "1.0",
                    "RMPS": "0",
                    "MOUT": "0.0",
                    "ULIM": "10.0",
                    "LLIM": "-10.0",
                    "VOLT": "0.0",
                    "EXON": "0",
                },
            )

        def conn(m):
            frames["slot"] = int(m.group(1))
            return ""

        def getter(m):
            return str(module().get(m.group(1), "0"))

        def setter(m):
            module()[m.group(1)] = m.group(2)
            return ""

        return {
            re.compile(r'CONN (\d+), ".*"$'): conn,
            "*IDN?": "Stanford_Research_Systems,SIM960,s/n012345,ver2.17",
            # monitors mirror internal state where sensible; MMON tracks the
            # manual output through a sinusoid so bias-lock sweeps see a
            # realistic IM transfer function in sim
            "SMON?": lambda _: module()["SETP"],
            "MMON?": lambda _: f"{0.5 * math.sin(2.0 * float(module()['MOUT']) + 1.0):.4f}",
            "EMON?": lambda _: "0.000",
            "OMON?": lambda _: module()["MOUT"],
            "OPON": lambda _: module().__setitem__("EXON", "1") or "",
            "OPOF": lambda _: module().__setitem__("EXON", "0") or "",
            re.compile(
                r"(PCTL|ICTL|DCTL|OCTL|AMAN|INPT|RAMP|RMPS|EXON|GAIN|INTG|DERV|OFST|SETP|RATE|MOUT|ULIM|LLIM|VOLT)\?$"
            ): getter,
            re.compile(
                r"(PCTL|ICTL|DCTL|OCTL|AMAN|INPT|RAMP|STRT|GAIN|INTG|DERV|OFST|SETP|RATE|MOUT|ULIM|LLIM|VOLT)\s?(-?[\d.eE+-]+)$"
            ): setter,
        }


class _Module:
    """Common plumbing for SIM900 plug-in modules."""

    def __init__(self, mainframe: SIM900, slot: int, name: str = ""):
        self.mainframe = mainframe
        self.slot = slot
        self.name = name or f"{type(self).__name__}@slot{slot}"
        self.log = mainframe.log

    def query(self, cmd: str) -> str:
        return self.mainframe.query_slot(self.slot, cmd)

    def write(self, cmd: str) -> None:
        self.mainframe.write_slot(self.slot, cmd)

    def _get_bool(self, cmd: str) -> bool:
        reply = self.query(f"{cmd}?").strip().casefold()
        if reply in ("1", "on"):
            return True
        if reply in ("0", "off"):
            return False
        raise ResponseError(f"{self.name}: bad {cmd} reply {reply!r}")

    def _set_bool(self, cmd: str, value) -> None:
        self.write(f"{cmd}{_as_onoff(value)}")

    def _get_num(self, cmd: str) -> float:
        return float(self.query(f"{cmd}?").replace(" ", ""))

    def _set_num(
        self,
        cmd: str,
        value: float,
        low: float | None = None,
        high: float | None = None,
        resolution: float | None = None,
    ) -> None:
        value = float(value)
        if low is not None and value < low:
            raise ValueError(f"{self.name}: {cmd} {value} below minimum {low}")
        if high is not None and value > high:
            raise ValueError(f"{self.name}: {cmd} {value} above maximum {high}")
        if resolution is not None:
            decimals = int(-log10(resolution))
            value = round(value, decimals)
            self.write(f"{cmd}{value:.{decimals}f}")
        else:
            self.write(f"{cmd}{value}")

    @property
    def identity(self) -> str:
        return self.query("*IDN?")

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.name!r} slot {self.slot}>"


class SIM960(_Module):
    """SIM960 analog PID controller module.

    Ranges/resolutions from the SIM960 manual, as encoded in the old
    driver: P gain ±1e3 (0.1), I gain 1e-2..5e5 s^-1 (0.01), D gain
    1e-6..10 s (1e-6), setpoint/offset/output ±10 V (1 mV).
    """

    #: ramp step (V) for manual-output moves; 0 disables ramping
    manual_output_ramp: float = 0.0

    def __init__(self, mainframe: SIM900, slot: int, name: str = ""):
        super().__init__(mainframe, slot, name)
        # Software limits, narrowable per lock (old set_manual_output_max/min)
        self.manual_output_min = -10.0
        self.manual_output_max = 10.0

    # PID terms ------------------------------------------------------------

    @property
    def proportional_on(self) -> bool:
        return self._get_bool("PCTL")

    @proportional_on.setter
    def proportional_on(self, value) -> None:
        self._set_bool("PCTL", value)

    @property
    def integral_on(self) -> bool:
        return self._get_bool("ICTL")

    @integral_on.setter
    def integral_on(self, value) -> None:
        self._set_bool("ICTL", value)

    @property
    def derivative_on(self) -> bool:
        return self._get_bool("DCTL")

    @derivative_on.setter
    def derivative_on(self, value) -> None:
        self._set_bool("DCTL", value)

    @property
    def offset_on(self) -> bool:
        return self._get_bool("OCTL")

    @offset_on.setter
    def offset_on(self, value) -> None:
        self._set_bool("OCTL", value)

    @property
    def proportional_gain(self) -> float:
        return self._get_num("GAIN")

    @proportional_gain.setter
    def proportional_gain(self, value: float) -> None:
        # sign encodes polarity; magnitude 0.1 .. 1e3
        self._set_num("GAIN", value, low=-1e3, high=1e3, resolution=0.1)

    @property
    def integral_gain(self) -> float:
        return self._get_num("INTG")

    @integral_gain.setter
    def integral_gain(self, value: float) -> None:
        self._set_num("INTG", value, low=1e-2, high=5e5, resolution=1e-2)

    @property
    def derivative_gain(self) -> float:
        return self._get_num("DERV")

    @derivative_gain.setter
    def derivative_gain(self, value: float) -> None:
        self._set_num("DERV", value, low=1e-6, high=10, resolution=1e-6)

    @property
    def output_offset_V(self) -> float:
        return self._get_num("OFST")

    @output_offset_V.setter
    def output_offset_V(self, value: float) -> None:
        self._set_num("OFST", value, low=-10, high=10)

    # Output mode / setpoint -------------------------------------------------

    @property
    def output_mode(self) -> str:
        """``"MAN"`` (manual) or ``"PID"`` (locked)."""
        reply = self.query("AMAN?").strip()
        if reply == "0":
            return "MAN"
        if reply == "1":
            return "PID"
        raise ResponseError(f"{self.name}: bad AMAN reply {reply!r}")

    @output_mode.setter
    def output_mode(self, mode: str) -> None:
        text = str(mode).strip().casefold()
        if text in ("1", "pid"):
            self.write("AMAN1")
        elif text in ("0", "man", "manual"):
            self.write("AMAN0")
        else:
            raise ValueError(f"output_mode must be 'PID' or 'MAN', got {mode!r}")

    @property
    def setpoint_source(self) -> str:
        """``"INT"`` (internal SETP) or ``"EXT"`` (front-panel input)."""
        reply = self.query("INPT?").strip()
        if reply == "0":
            return "INT"
        if reply == "1":
            return "EXT"
        raise ResponseError(f"{self.name}: bad INPT reply {reply!r}")

    @setpoint_source.setter
    def setpoint_source(self, source: str) -> None:
        text = str(source).strip().casefold()
        if text in ("1", "ext", "external"):
            self.write("INPT1")
        elif text in ("0", "int", "internal"):
            self.write("INPT0")
        else:
            raise ValueError(f"setpoint_source must be 'INT' or 'EXT', got {source!r}")

    @property
    def setpoint_V(self) -> float:
        return self._get_num("SETP")

    @setpoint_V.setter
    def setpoint_V(self, value: float) -> None:
        self._set_num("SETP", value, low=-10, high=10, resolution=1e-3)

    # Setpoint ramping -------------------------------------------------------

    @property
    def setpoint_ramping_on(self) -> bool:
        return self._get_bool("RAMP")

    @setpoint_ramping_on.setter
    def setpoint_ramping_on(self, value) -> None:
        self._set_bool("RAMP", value)

    @property
    def setpoint_ramp_rate(self) -> float:
        return self._get_num("RATE")

    @setpoint_ramp_rate.setter
    def setpoint_ramp_rate(self, value: float) -> None:
        self._set_num("RATE", value, low=1e-3, high=1e4)

    # Manual output ----------------------------------------------------------

    @property
    def manual_output_V(self) -> float:
        return self._get_num("MOUT")

    @manual_output_V.setter
    def manual_output_V(self, value: float) -> None:
        value = float(value)
        if not self.manual_output_min <= value <= self.manual_output_max:
            raise ValueError(
                f"{self.name}: MOUT {value} V outside "
                f"[{self.manual_output_min}, {self.manual_output_max}] V"
            )
        if self.manual_output_ramp == 0:
            self._set_num("MOUT", value, resolution=1e-3)
            return
        start = self.manual_output_V
        step = abs(self.manual_output_ramp)
        n_steps = max(int(np.ceil(abs(start - value) / step)), 2)
        for volt in np.round(np.linspace(start, value, n_steps) * 1000) / 1000:
            self.write(f"MOUT{volt:.3f}")
        self.log.info("%s: manual output ramped to %.3f V", self.name, value)

    # Output limits ----------------------------------------------------------

    @property
    def output_upper_limit_V(self) -> float:
        return self._get_num("ULIM")

    @output_upper_limit_V.setter
    def output_upper_limit_V(self, value: float) -> None:
        self._set_num("ULIM", value, low=-10, high=10, resolution=1e-2)

    @property
    def output_lower_limit_V(self) -> float:
        return self._get_num("LLIM")

    @output_lower_limit_V.setter
    def output_lower_limit_V(self, value: float) -> None:
        self._set_num("LLIM", value, low=-10, high=10, resolution=1e-2)

    # Monitors ---------------------------------------------------------------

    @property
    def setpoint_input_V(self) -> float:
        return self._get_num("SMON")

    @property
    def measure_input_V(self) -> float:
        return self._get_num("MMON")

    @property
    def amplified_error_V(self) -> float:
        return self._get_num("EMON")

    @property
    def output_V(self) -> float:
        return self._get_num("OMON")

    def status(self) -> dict:
        return {
            "output_mode": self.output_mode,
            "setpoint_V": self.setpoint_V,
            "manual_output_V": self.manual_output_V,
            "measure_input_V": self.measure_input_V,
            "output_V": self.output_V,
            "proportional_gain": self.proportional_gain,
            "integral_gain": self.integral_gain,
        }


class SIM928(_Module):
    """SIM928 isolated voltage source (a fleshed-out port — the old class
    was an empty shell with only read/write plumbing)."""

    @property
    def voltage_V(self) -> float:
        return self._get_num("VOLT")

    @voltage_V.setter
    def voltage_V(self, value: float) -> None:
        self._set_num("VOLT", value, low=-20, high=20, resolution=1e-3)

    @property
    def output_on(self) -> bool:
        return self._get_bool("EXON")

    @output_on.setter
    def output_on(self, value) -> None:
        self.write("OPON" if _as_onoff(value) == "1" else "OPOF")

    def status(self) -> dict:
        return {"voltage_V": self.voltage_V, "output_on": self.output_on}
