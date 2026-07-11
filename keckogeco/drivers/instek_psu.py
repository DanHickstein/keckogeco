"""GW Instek DC power supplies (GPD-4303S and GPP series).

One driver for both families — the rack uses a GPD-4303S (RF oscillator
supply + IM attenuator bias) and a GPP-1326 (RF amplifier supply, ~120 W).
The families share the ``VSETn``/``ISETn``/``VOUTn``/``IOUTn`` command set
but differ in serial settings, output switching, and channel limits, which
live in :data:`PROFILES`.

Merged from ``Hardware/InstekGPD_4303S.py`` and
``Hardware/InstekGppDCSupply.py``. Behaviors preserved:

* setpoint writes are re-sent until the readback matches (3 s timeout);
* channel/voltage/current limits **raise** on violation, including the
  GPD-4303S CH3 coupled limit (3 A below 5 V, 1 A above);
* GPD output is a master switch (``OUT0/1``); GPP outputs are per-channel
  (``:OUTPn:STAT``).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import ClassVar

from ..config import DeviceConfig
from .base import Instrument
from .errors import ResponseError
from .transports import Transport

__all__ = ["PROFILES", "InstekPSU"]


@dataclass(frozen=True)
class _Profile:
    channels: int
    baud_rate: int
    read_termination: str
    master_output: bool  # True: OUT0/1 master switch; False: per-channel
    # (voltage_max, current_max) per channel, 1-indexed; None = unspecified
    limits: tuple[tuple[float, float], ...]


PROFILES: dict[str, _Profile] = {
    "GPD-4303S": _Profile(
        channels=4,
        baud_rate=9600,
        read_termination="\r\n",
        master_output=True,
        limits=((30.0, 3.0), (30.0, 3.0), (10.0, 3.0), (5.0, 1.0)),
    ),
    "GPP-1326": _Profile(
        channels=1,
        baud_rate=115200,
        read_termination="\n",
        master_output=False,
        limits=((32.0, 6.2),),
    ),
    "GPP-2323": _Profile(
        channels=2,
        baud_rate=115200,
        read_termination="\n",
        master_output=False,
        limits=((32.0, 6.2), (32.0, 6.2)),
    ),
    "GPP-3323": _Profile(
        channels=3,
        baud_rate=115200,
        read_termination="\n",
        master_output=False,
        limits=((32.0, 6.2), (32.0, 6.2), (5.5, 1.0)),
    ),
    "GPP-4323": _Profile(
        channels=4,
        baud_rate=115200,
        read_termination="\n",
        master_output=False,
        limits=((32.0, 6.2), (32.0, 6.2), (5.5, 1.0), (15.0, 1.0)),
    ),
}


def _strip_unit(reply: str) -> float:
    """Parse ``'3.000A'``, ``'250mV'`` or plain ``'3.000'`` to base units."""
    text = reply.strip()
    lowered = text.casefold()
    if lowered[-2:] in ("ma", "mv"):
        return float(text[:-2]) / 1000
    if lowered[-1:] in ("a", "v", "w"):
        return float(text[:-1])
    return float(text)


class InstekPSU(Instrument):
    """GW Instek DC supply. Voltages in V, currents in A.

    Parameters
    ----------
    transport : Transport
    name : str
    model : str
        Key into :data:`PROFILES`; set via the ``model`` config option.
    """

    DRIVER_OPTIONS: ClassVar[tuple[str, ...]] = ("model",)
    SETPOINT_TIMEOUT_S: ClassVar[float] = 3.0

    def __init__(self, transport: Transport, name: str = "", model: str = "GPD-4303S"):
        super().__init__(transport, name)
        if model not in PROFILES:
            raise ValueError(f"{name}: unknown Instek model {model!r} (know {sorted(PROFILES)})")
        self.model = model
        self.profile = PROFILES[model]

    @classmethod
    def transport_defaults(cls, cfg: DeviceConfig) -> dict:
        profile = PROFILES.get(cfg.options.get("model", "GPD-4303S"))
        if profile is None:
            raise ValueError(f"[devices.{cfg.key}] unknown Instek model")
        return {
            "timeout_ms": 25_000,
            "baud_rate": profile.baud_rate,
            "read_termination": profile.read_termination,
        }

    def _check_channel(self, channel: int) -> None:
        if not 1 <= channel <= self.profile.channels:
            raise ValueError(
                f"{self.name}: channel {channel} out of range for {self.model} "
                f"(1..{self.profile.channels})"
            )

    # ------------------------------------------------------------- identity

    @property
    def identity(self) -> str:
        return self.query("*IDN?")

    @property
    def serial_number(self) -> str:
        parts = self.identity.split(",")
        return parts[2].strip() if len(parts) > 2 else ""

    # ------------------------------------------------------------ setpoints

    def voltage_setpoint_V(self, channel: int = 1) -> float:
        self._check_channel(channel)
        return _strip_unit(self.query(f"VSET{channel}?"))

    def current_setpoint_A(self, channel: int = 1) -> float:
        self._check_channel(channel)
        return _strip_unit(self.query(f"ISET{channel}?"))

    def set_voltage_V(self, voltage: float, channel: int = 1) -> None:
        self._check_channel(channel)
        v_max, _ = self.profile.limits[channel - 1]
        if voltage > v_max:
            raise ValueError(
                f"{self.name}: VSET {voltage} V on CH{channel} exceeds {self.model} max {v_max} V"
            )
        if (
            self.model == "GPD-4303S"
            and channel == 3
            and voltage > 5
            and self.current_setpoint_A(3) > 1
        ):
            raise ValueError(
                f"{self.name}: CH3 supports 3 A only below 5 V; lower ISET to <=1 A first"
            )
        self._set_verified(
            f"VSET{channel}:{voltage:.3f}", lambda: self.voltage_setpoint_V(channel), voltage
        )

    def set_current_A(self, current: float, channel: int = 1) -> None:
        self._check_channel(channel)
        _, i_max = self.profile.limits[channel - 1]
        if current > i_max:
            raise ValueError(
                f"{self.name}: ISET {current} A on CH{channel} exceeds {self.model} max {i_max} A"
            )
        if (
            self.model == "GPD-4303S"
            and channel == 3
            and current > 1
            and self.voltage_setpoint_V(3) > 5
        ):
            raise ValueError(
                f"{self.name}: CH3 supports >1 A only below 5 V; lower VSET to <=5 V first"
            )
        self._set_verified(
            f"ISET{channel}:{current:.3f}", lambda: self.current_setpoint_A(channel), current
        )

    def _set_verified(self, cmd: str, readback, target: float) -> None:
        """Send `cmd` until `readback()` is within 1 mV/mA of `target`."""
        deadline = time.monotonic() + self.SETPOINT_TIMEOUT_S
        while abs(readback() - target) > 0.001:
            if time.monotonic() > deadline:
                raise RuntimeError(f"{self.name}: {cmd!r} not confirmed within timeout")
            self.write(cmd)
        self.log.info("%s: %s confirmed", self.name, cmd)

    # -------------------------------------------------------------- outputs

    def output_voltage_V(self, channel: int = 1) -> float:
        self._check_channel(channel)
        return _strip_unit(self.query(f"VOUT{channel}?"))

    def output_current_A(self, channel: int = 1) -> float:
        self._check_channel(channel)
        return _strip_unit(self.query(f"IOUT{channel}?"))

    def output_power_W(self, channel: int = 1) -> float:
        return self.output_voltage_V(channel) * self.output_current_A(channel)

    # ------------------------------------------------------- output switching

    def output_on(self, channel: int = 1) -> bool:
        """Output state (master switch for GPD; per-channel for GPP)."""
        if self.profile.master_output:
            return self._status_code()[5] == "1"
        self._check_channel(channel)
        reply = self.query(f":OUTP{channel}:STAT?").strip().upper()
        if reply not in ("ON", "OFF"):
            raise ResponseError(f"{self.name}: bad output state reply {reply!r}")
        return reply == "ON"

    def set_output(self, on: bool, channel: int = 1) -> None:
        if self.profile.master_output:
            self.write("OUT1" if on else "OUT0")
            self.log.info("%s: master output %s", self.name, "ON" if on else "OFF")
            return
        self._check_channel(channel)
        target = "ON" if on else "OFF"
        deadline = time.monotonic() + self.SETPOINT_TIMEOUT_S
        while self.output_on(channel) != bool(on):
            if time.monotonic() > deadline:
                raise RuntimeError(f"{self.name}: CH{channel} output did not turn {target}")
            self.write(f":OUTP{channel}:STAT {'1' if on else '0'}")
        self.log.info("%s: CH%d output %s", self.name, channel, target)

    def _status_code(self) -> str:
        """GPD-4303S STATUS? bits: CH1/CH2 CC-CV, tracking, beep, output."""
        reply = self.query("STATUS?").strip()
        if len(reply) < 6 or not set(reply) <= {"0", "1"}:
            raise ResponseError(f"{self.name}: bad STATUS? reply {reply!r}")
        return reply

    # ---------------------------------------------------------------- misc

    def all_zero(self) -> None:
        """Set every channel's V and I setpoints to zero."""
        for channel in range(1, self.profile.channels + 1):
            self.set_current_A(0, channel)
            self.set_voltage_V(0, channel)

    def status(self) -> dict:
        channels = {}
        for ch in range(1, self.profile.channels + 1):
            channels[ch] = {
                "V_set": self.voltage_setpoint_V(ch),
                "I_set": self.current_setpoint_A(ch),
                "V_out": self.output_voltage_V(ch),
                "I_out": self.output_current_A(ch),
            }
        return {
            "model": self.model,
            "output_on": self.output_on(1),
            "channels": channels,
        }

    # ----------------------------------------------------------------- sim

    @classmethod
    def sim_responses(cls) -> dict:
        import re

        state = {"out": "0", "vset": {}, "iset": {}}

        def get(store, default="0.000"):
            return lambda m: state[store].get(m.group(1), default)

        def put(store):
            def _put(m):
                state[store][m.group(1)] = f"{float(m.group(2)):.3f}"
                return ""

            return _put

        def set_out(m):
            state["out"] = m.group(1)
            return ""

        def set_channel_out(m):
            state["out"] = m.group(2)
            return ""

        return {
            "*IDN?": "GW-INSTEK,GPD-4303S,SN000000,V1.00",
            "STATUS?": lambda _: f"01011{state['out']}01"[:8],
            re.compile(r"VSET(\d)\?"): get("vset"),
            re.compile(r"ISET(\d)\?"): get("iset"),
            re.compile(r"VSET(\d):([\d.]+)$"): put("vset"),
            re.compile(r"ISET(\d):([\d.]+)$"): put("iset"),
            re.compile(r"VOUT(\d)\?"): lambda m: state["vset"].get(m.group(1), "0.000") + "V",
            re.compile(r"IOUT(\d)\?"): lambda m: state["iset"].get(m.group(1), "0.000") + "A",
            re.compile(r"OUT(\d)$"): set_out,
            re.compile(r":OUTP(\d):STAT\?"): lambda _: "ON" if state["out"] == "1" else "OFF",
            re.compile(r":OUTP(\d):STAT (\d)$"): set_channel_out,
        }
