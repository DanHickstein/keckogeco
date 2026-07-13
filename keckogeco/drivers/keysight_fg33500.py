"""Keysight 33500-series function generator (two channels, USB-TMC SCPI).

Two units on the rack drive RF-chain modulation. Plain SCPI; all
channel-scoped settings go through ``SOUR<n>:...`` / ``OUTP<n>``.
Ported from ``Hardware/KeysightFG_33500.py``.
"""

from __future__ import annotations

import re
from typing import ClassVar

from .base import Instrument
from .transports import Transport

__all__ = ["KeysightFG33500"]


class KeysightFG33500(Instrument):
    """33500-series FG; every accessor takes the channel (1 or 2)."""

    TRANSPORT_DEFAULTS: ClassVar[dict] = {"timeout_ms": 5_000}

    CHANNELS: ClassVar[tuple[int, ...]] = (1, 2)
    #: accepted spellings -> SCPI function names
    FUNCTIONS: ClassVar[dict[str, str]] = {
        "sin": "SIN",
        "sine": "SIN",
        "squ": "SQU",
        "square": "SQU",
        "ramp": "RAMP",
        "dc": "DC",
    }

    def __init__(self, transport: Transport, name: str = ""):
        super().__init__(transport, name)

    @staticmethod
    def _ch(channel: int) -> int:
        channel = int(channel)
        if channel not in KeysightFG33500.CHANNELS:
            raise ValueError(f"channel must be 1 or 2, got {channel}")
        return channel

    def _query(self, cmd: str) -> str:
        return self._io(lambda: self.transport.query(cmd)).strip()

    def _write(self, cmd: str) -> None:
        self._io(lambda: self.transport.write(cmd))

    def idn(self) -> str:
        return self._query("*IDN?")

    # ------------------------------------------------------------ waveform

    def frequency_Hz(self, channel: int) -> float:
        return float(self._query(f"SOUR{self._ch(channel)}:FREQ?"))

    def set_frequency_Hz(self, channel: int, freq_Hz: float) -> None:
        self._write(f"SOUR{self._ch(channel)}:FREQ {float(freq_Hz):.6f}")

    def amplitude_V(self, channel: int) -> float:
        return float(self._query(f"SOUR{self._ch(channel)}:VOLT?"))

    def set_amplitude_V(self, channel: int, volts: float) -> None:
        self._write(f"SOUR{self._ch(channel)}:VOLT {float(volts):.6f}")

    def offset_V(self, channel: int) -> float:
        return float(self._query(f"SOUR{self._ch(channel)}:VOLT:OFFS?"))

    def set_offset_V(self, channel: int, volts: float) -> None:
        self._write(f"SOUR{self._ch(channel)}:VOLT:OFFS {float(volts):.6f}")

    def phase_deg(self, channel: int) -> float:
        return float(self._query(f"SOUR{self._ch(channel)}:PHAS?"))

    def set_phase_deg(self, channel: int, degrees: float) -> None:
        self._write(f"SOUR{self._ch(channel)}:PHAS {float(degrees):.6f}")

    def function(self, channel: int) -> str:
        """Active waveform, e.g. ``SIN``, ``SQU``, ``RAMP``, ``DC``."""
        return self._query(f"SOUR{self._ch(channel)}:FUNC?")

    def set_function(self, channel: int, func: str) -> None:
        scpi = self.FUNCTIONS.get(str(func).casefold())
        if scpi is None:
            raise ValueError(f"function must be one of {sorted(set(self.FUNCTIONS))}, got {func!r}")
        self._write(f"SOUR{self._ch(channel)}:FUNC {scpi}")

    # -------------------------------------------------------------- output

    def output(self, channel: int) -> bool:
        return self._query(f"OUTP{self._ch(channel)}?").rstrip() in ("1", "ON")

    def set_output(self, channel: int, on: bool) -> None:
        self._write(f"OUTP{self._ch(channel)} {'ON' if on else 'OFF'}")
        self.log.info("%s: channel %d output -> %s", self.name, channel, "ON" if on else "OFF")

    # ------------------------------------------------------------- summary

    def channel_parameters(self, channel: int) -> dict:
        """Everything about one channel (the old LFC_FUNCTION_GEN_STATE dump)."""
        channel = self._ch(channel)
        return {
            "channel": channel,
            "function": self.function(channel),
            "frequency_Hz": self.frequency_Hz(channel),
            "amplitude_V": self.amplitude_V(channel),
            "offset_V": self.offset_V(channel),
            "phase_deg": self.phase_deg(channel),
            "output": self.output(channel),
        }

    def status(self) -> dict:
        return {f"ch{ch}": self.channel_parameters(ch) for ch in self.CHANNELS}

    @classmethod
    def sim_responses(cls) -> dict:
        state = {
            ch: {
                "FREQ": "1000.0",
                "VOLT": "0.1",
                "OFFS": "0.0",
                "PHAS": "0.0",
                "FUNC": "SIN",
                "OUTP": "0",
            }
            for ch in cls.CHANNELS
        }

        def getter(field):
            return lambda m: state[int(m.group(1))][field]

        def setter(field, value=None):
            def apply(m):
                state[int(m.group(1))][field] = value(m) if value else m.group(2)
                return ""

            return apply

        return {
            "*IDN?": "Agilent Technologies,33512B,MY59003824,2.03",
            # OFFS before bare VOLT so the more specific command wins
            re.compile(r"SOUR([12]):VOLT:OFFS\?"): getter("OFFS"),
            re.compile(r"SOUR([12]):VOLT:OFFS (\S+)"): setter("OFFS"),
            re.compile(r"SOUR([12]):FREQ\?"): getter("FREQ"),
            re.compile(r"SOUR([12]):FREQ (\S+)"): setter("FREQ"),
            re.compile(r"SOUR([12]):VOLT\?"): getter("VOLT"),
            re.compile(r"SOUR([12]):VOLT (\S+)"): setter("VOLT"),
            re.compile(r"SOUR([12]):PHAS\?"): getter("PHAS"),
            re.compile(r"SOUR([12]):PHAS (\S+)"): setter("PHAS"),
            re.compile(r"SOUR([12]):FUNC\?"): getter("FUNC"),
            re.compile(r"SOUR([12]):FUNC (\S+)"): setter("FUNC"),
            re.compile(r"OUTP([12])\?"): getter("OUTP"),
            re.compile(r"OUTP([12]) (ON|OFF)"): setter(
                "OUTP", lambda m: "1" if m.group(2) == "ON" else "0"
            ),
        }
