"""Pendulum CNT-90 microwave frequency counter.

Measures the comb repetition rate (16 GHz on channel C). Its reading is
the sixth factor of the comb state check: with both RF supplies on, the
rep rate must be within 1 kHz of 16 GHz. Ported from
``Hardware/PendulumCNT90.py`` (minus the stdin-confirmed reset).
"""

from __future__ import annotations

from typing import ClassVar

from .base import Instrument
from .errors import ResponseError

__all__ = ["PendulumCNT90"]

_CHANNELS = {"a": "(@1)", "b": "(@2)", "c": "(@3)", "1": "(@1)", "2": "(@2)", "3": "(@3)"}


class PendulumCNT90(Instrument):
    """CNT-90 on GPIB. Frequencies in Hz."""

    TRANSPORT_DEFAULTS: ClassVar[dict] = {
        # generous: FETC? blocks until the aperture completes
        "timeout_ms": 30_000,
        "read_termination": "\n",
    }

    @property
    def identity(self) -> str:
        return self.query("*IDN?")

    @property
    def case_temperature_C(self) -> float:
        return float(self.query(":SYST:TEMP?"))

    def run(self) -> None:
        """Continuous measurement, updating the front-panel display."""
        self.write(":INIT:CONT ON")
        self.write(":SENS:TOT:GATE ON")

    def stop(self) -> None:
        self.write(":INIT:CONT OFF")
        self.write(":SENS:TOT:GATE OFF")

    def measure_frequency_Hz(self, channel: str = "c", meas_time_s: float = 0.1) -> float:
        """One frequency measurement on a channel (A/B/C or 1/2/3)."""
        key = str(channel).casefold()
        if key not in _CHANNELS:
            raise ValueError(f"channel must be one of {sorted(set(_CHANNELS))}, got {channel!r}")
        if not 20e-9 <= meas_time_s <= 1000:
            raise ValueError(f"meas_time_s must be within 20 ns .. 1000 s, got {meas_time_s}")
        self.write(f":CONF:FREQ {_CHANNELS[key]}")
        self.write(f":ACQ:APER {meas_time_s}")
        self.write(":INIT")
        reply = self.query("FETC?")
        try:
            return float(reply)
        except ValueError as exc:
            raise ResponseError(
                f"{self.name}: FETC? returned {reply!r} - check the signal input"
            ) from exc

    def status(self) -> dict:
        return {"frequency_Hz": self.measure_frequency_Hz()}

    SIM_RESPONSES: ClassVar[dict] = {
        "*IDN?": "Pendulum, CNT-90XL, 0, SIM",
        ":SYST:TEMP?": "38.0",
        "FETC?": "16000000000.0",  # a healthy 16 GHz rep rate
    }
