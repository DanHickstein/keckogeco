"""Measurement Computing USB-2408 thermocouple DAQ.

Two boards monitor rack and optical-table temperatures through type-J
thermocouples. Driven through the vendor ``mcculw`` Universal Library
(installed with MCC InstaCal), imported lazily so the package works
on non-Windows machines. Ported from ``Hardware/USB2408.py``.

``address`` in config is the board's USB serial number (e.g.
``"205F843"``), matching the serial-anchored convention used for the
COM-port instruments. Boards are bound directly from the USB inventory
(``ul.ignore_instacal()`` + ``ul.create_daq_device()``); InstaCal never
needs to be run, so there is no CB.CFG board-number table to maintain.

The channel-position labels documented at commissioning (Jun 2023) are
kept as defaults for the two known serials and can be overridden with a
``positions`` list in the device's config block.
"""

from __future__ import annotations

import math
import threading
from typing import ClassVar

from ..config import DeviceConfig
from .base import Instrument
from .errors import InstrumentError
from .transports import Transport

__all__ = ["USB2408"]

NUM_CHANNELS = 8

DEFAULT_POSITIONS = {
    "205F843": [
        "Rack side baffle (middle side rack)",
        "Waveshaper (upper rack)",
        "Rb clock (middle rack)",
        "Pritel (middle upper rack)",
        "Rack glycol out",
        "Rack glycol in",
        "Power supply shelf (bottom rack)",
        "Unconnected",
    ],
    "205F82F": [
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


# mcculw board numbers are process-global slots; claim them under a lock
# so the two boards (and reconnects) never collide.
_UL_LOCK = threading.Lock()
_BOARDS_IN_USE: set[int] = set()
_INSTACAL_IGNORED = False


def ignore_instacal_once() -> None:
    """Call ``ul.ignore_instacal()`` exactly once per process.

    ``cbIgnoreInstaCal()`` resets the UL's internal device table,
    releasing every board bound with ``create_daq_device`` — calling it
    on every ``open()`` invalidated the *other* board's binding (UL
    Error 1 on each poll, with each reconnect breaking the other board
    in a permanent ping-pong). Anything that touches mcculw in a
    process that may also hold open boards must go through this guard.
    """
    global _INSTACAL_IGNORED
    from mcculw import ul

    with _UL_LOCK:
        if not _INSTACAL_IGNORED:
            ul.ignore_instacal()
            _INSTACAL_IGNORED = True


class UlLink:
    """Transport-shaped wrapper over the mcculw Universal Library."""

    def __init__(self, address: str):
        self.address = address  # USB serial number, e.g. "205F843"
        self.board = -1  # assigned from the free slots at open()
        self._open = False

    def open(self) -> None:
        if self._open:
            return
        try:
            from mcculw import ul
            from mcculw.enums import InterfaceType
        except ImportError as exc:
            raise InstrumentError(
                "The 'mcculw' package (and the MCC Universal Library it "
                "wraps) is required for the USB-2408; install it or run "
                "in --sim mode."
            ) from exc
        serial = str(self.address).strip().upper()
        ignore_instacal_once()
        with _UL_LOCK:
            devices = ul.get_daq_device_inventory(InterfaceType.ANY)
            for dev in devices:
                if dev.unique_id.strip().upper() == serial:
                    break
            else:
                found = ", ".join(f"{d.product_name} {d.unique_id}" for d in devices) or "none"
                raise InstrumentError(f"No DAQ with USB serial {serial}; found: {found}")
            if "USB-2408" not in dev.product_name:
                raise InstrumentError(f"DAQ {serial} is a {dev.product_name!r}, not a USB-2408")
            self.board = next(n for n in range(64) if n not in _BOARDS_IN_USE)
            try:
                ul.create_daq_device(self.board, dev)
            except Exception as exc:
                self.board = -1
                raise InstrumentError(f"Could not bind DAQ {serial}: {exc}") from exc
            _BOARDS_IN_USE.add(self.board)
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
        if not self._open:
            return
        self._open = False
        board, self.board = self.board, -1
        with _UL_LOCK:
            _BOARDS_IN_USE.discard(board)
            try:
                from mcculw import ul

                ul.release_daq_device(board)
            except Exception:  # noqa: BLE001, S110 - closing must never raise
                pass

    @property
    def is_open(self) -> bool:
        return self._open

    def read_temperature_C(self, channel: int) -> float:
        from mcculw import ul
        from mcculw.enums import ErrorCode, TempScale, TInOptions

        try:
            return float(ul.t_in(self.board, channel, TempScale.CELSIUS, TInOptions.NOFILTER))
        except ul.ULError as exc:
            # an open/unconnected thermocouple is a real state, not a link
            # fault: NaN, without tripping the base class's reconnect-once
            # (the rack board's ch7 is permanently unconnected)
            if exc.errorcode == ErrorCode.OPENCONNECTION:
                return math.nan
            raise

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

    def close(self) -> None:
        self._open = False

    def read_temperature_C(self, channel: int) -> float:
        return 23.0 + channel * 0.5


class USB2408(Instrument):
    """One USB-2408 board; ``address`` in config is the USB serial number."""

    DRIVER_OPTIONS: ClassVar[tuple[str, ...]] = ("positions",)

    def __init__(self, transport: Transport, name: str = "", positions: list[str] | None = None):
        super().__init__(transport, name)
        serial = str(transport.address).strip().upper()
        self.positions = list(positions or DEFAULT_POSITIONS.get(serial, ["?"] * NUM_CHANNELS))

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
