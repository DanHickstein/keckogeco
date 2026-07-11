"""Measurement Computing USB-2408 thermocouple DAQ.

Two boards monitor rack and optical-table temperatures through type-J
thermocouples. Driven through the vendor ``mcculw`` Universal Library
(requires InstaCal on the laptop), imported lazily so the package works
on non-Windows machines. Ported from ``Hardware/USB2408.py``.

The channel-position labels documented at commissioning (Jun 2023) are
kept as defaults for boards 0 and 1 and can be overridden with a
``positions`` list in the device's config block.
"""

from __future__ import annotations

import math
from typing import ClassVar

from ..config import DeviceConfig
from .base import Instrument
from .errors import InstrumentError
from .transports import Transport

__all__ = ["USB2408"]

NUM_CHANNELS = 8

DEFAULT_POSITIONS = {
    0: [
        "Rack side baffle (middle side rack)",
        "Waveshaper (upper rack)",
        "Rb clock (middle rack)",
        "Pritel (middle upper rack)",
        "Rack glycol out",
        "Rack glycol in",
        "Power supply shelf (bottom rack)",
        "Unconnected",
    ],
    1: [
        "RF oscillator",
        "RF amplifier",
        "Main phase modulators",
        "Filter cavity",
        "Board glycol out",
        "Board glycol in",
        "Compression stage",
        "Rubidium (Rb) cell D2-210",
    ],
}


class UlLink:
    """Transport-shaped wrapper over the mcculw Universal Library."""

    def __init__(self, address: str):
        self.address = address  # board number as a string
        self.board = int(address)
        self._open = False

    def open(self) -> None:
        if self._open:
            return
        try:
            from mcculw import ul
        except ImportError as exc:
            raise InstrumentError(
                "The 'mcculw' package (and InstaCal) is required for the "
                "USB-2408; install it or run in --sim mode."
            ) from exc
        try:
            board_name = ul.get_board_name(self.board)
        except Exception as exc:
            raise InstrumentError(f"No DAQ board at number {self.board}; check InstaCal") from exc
        if "USB-2408" not in board_name:
            raise InstrumentError(
                f"Board {self.board} is {board_name!r}, not a USB-2408; check InstaCal"
            )
        ul.flash_led(self.board)
        self._configure_channels()
        self._open = True

    def _configure_channels(self) -> None:
        from mcculw import ul
        from mcculw.enums import AiChanType, BoardInfo, InfoType, TcType, TempScale

        for channel in range(NUM_CHANNELS):
            ul.set_config(
                InfoType.BOARDINFO, self.board, channel, BoardInfo.ADCHANTYPE, AiChanType.TC
            )
            ul.set_config(InfoType.BOARDINFO, self.board, channel, BoardInfo.CHANTCTYPE, TcType.J)
            ul.set_config(
                InfoType.BOARDINFO, self.board, channel, BoardInfo.TEMPSCALE, TempScale.CELSIUS
            )
            ul.set_config(InfoType.BOARDINFO, self.board, channel, BoardInfo.ADDATARATE, 60)

    def close(self) -> None:
        self._open = False

    @property
    def is_open(self) -> bool:
        return self._open

    def read_temperature_C(self, channel: int) -> float:
        from mcculw import ul
        from mcculw.enums import TempScale, TInOptions

        return float(ul.t_in(self.board, channel, TempScale.CELSIUS, TInOptions.NOFILTER))

    # Transport protocol stubs
    def write(self, cmd: str) -> None:
        raise NotImplementedError("USB-2408 has no text command channel")

    read = query = write
    write_bytes = read_bytes = read_available = write

    def clear(self) -> None:  # noqa: D102 - protocol no-op
        pass


class SimUlLink(UlLink):
    """Offline stand-in returning plausible fixed temperatures."""

    def open(self) -> None:
        self._open = True

    def read_temperature_C(self, channel: int) -> float:
        return 23.0 + channel * 0.5


class USB2408(Instrument):
    """One USB-2408 board; ``address`` in config is the board number."""

    DRIVER_OPTIONS: ClassVar[tuple[str, ...]] = ("positions",)

    def __init__(self, transport: Transport, name: str = "", positions: list[str] | None = None):
        super().__init__(transport, name)
        board = int(transport.address) if str(transport.address).isdigit() else -1
        self.positions = list(positions or DEFAULT_POSITIONS.get(board, ["?"] * NUM_CHANNELS))

    @classmethod
    def from_config(cls, cfg: DeviceConfig, sim: bool = False) -> USB2408:
        link = SimUlLink(cfg.address) if sim else UlLink(cfg.address)
        return cls(link, cfg.key, positions=cfg.options.get("positions"))

    def temperature_C(self, channel: int) -> float:
        """One channel's thermocouple reading; NaN if the read fails."""
        if not 0 <= channel < NUM_CHANNELS:
            raise ValueError(f"channel {channel} outside 0..{NUM_CHANNELS - 1}")

        def op() -> float:
            return self.transport.read_temperature_C(channel)  # type: ignore[attr-defined]

        try:
            return self._io(op)
        except InstrumentError:
            self.log.warning("%s: channel %d read failed", self.name, channel)
            return math.nan

    def all_temperatures_C(self) -> dict[str, float]:
        """{position label: temperature} for every channel."""
        return {
            self.positions[channel]: self.temperature_C(channel) for channel in range(NUM_CHANNELS)
        }

    def status(self) -> dict:
        return {"temperatures_C": self.all_temperatures_C()}
