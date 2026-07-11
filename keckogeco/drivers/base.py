"""Instrument base class.

Every driver in :mod:`keckogeco.drivers` subclasses :class:`Instrument`.
Design rules (see the project plan):

* **No addresses in code.** Constructors take a :class:`Transport`;
  :meth:`Instrument.from_config` builds the right transport from a
  :class:`~keckogeco.config.DeviceConfig` block (or a
  :class:`~keckogeco.drivers.transports.SimTransport` in sim mode).
* **Persistent connections.** The old system connected and disconnected
  around every keyword access, padded with ``sleep(0.2)``. Here a device is
  connected once and I/O failures trigger a single automatic
  reconnect-and-retry (ported from the old ``Device._visa_io_with_reconnect``)
  before raising :class:`~keckogeco.drivers.errors.ConnectionLost`.
* **Thread-safe I/O.** Every read/write/query holds the instrument's lock,
  so GUI polling, monitor threads, and REST handlers cannot interleave on
  one serial port.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import ClassVar, Self

from ..config import DeviceConfig
from .errors import ConnectionLost, InstrumentError
from .transports import SerialTransport, SimTransport, SocketTransport, Transport, VisaTransport

__all__ = ["Instrument"]

# DeviceConfig "transport" option value -> transport class. Drivers pick a
# default via DEFAULT_TRANSPORT; a config block can override with
# transport = "serial" etc.
_TRANSPORTS = {
    "visa": VisaTransport,
    "serial": SerialTransport,
    "socket": SocketTransport,
}


class Instrument:
    """A physical instrument reachable through a :class:`Transport`.

    Parameters
    ----------
    transport : Transport
        The opened-or-openable byte pipe to the device.
    name : str
        Instance name used in log messages (e.g. ``"edfa27"``).

    Notes
    -----
    Subclasses implement protocol logic on top of :meth:`query`,
    :meth:`write`, :meth:`write_bytes` and :meth:`read_bytes`, and may
    override:

    ``_configure()``
        Called after every successful transport open (including automatic
        reconnects) — put ``*IDN?`` checks, mode setup, or buffer flushes
        here.
    ``SIM_RESPONSES``
        Class-level dict for :class:`SimTransport` so the driver produces
        plausible values in ``--sim`` mode.
    ``DEFAULT_TRANSPORT`` / ``TRANSPORT_DEFAULTS``
        Which transport :meth:`from_config` builds and with what keyword
        defaults (``baud_rate``, ``terminator``, ...).
    """

    DEFAULT_TRANSPORT: ClassVar[str] = "visa"
    TRANSPORT_DEFAULTS: ClassVar[dict] = {}
    SIM_RESPONSES: ClassVar[dict] = {}

    #: seconds to wait between reopening the transport and retrying I/O
    RECONNECT_SETTLE_S: ClassVar[float] = 0.2

    def __init__(self, transport: Transport, name: str = ""):
        self.transport = transport
        self.name = name or type(self).__name__
        self.lock = threading.RLock()
        self.log = logging.getLogger(f"keckogeco.drivers.{self.name}")

    # ------------------------------------------------------------- lifecycle

    @classmethod
    def from_config(cls, cfg: DeviceConfig, sim: bool = False) -> Self:
        """Build the instrument (and its transport) from a config block."""
        if sim:
            return cls(SimTransport(cls.SIM_RESPONSES, address=f"SIM::{cfg.address}"), cfg.key)
        options = dict(cls.TRANSPORT_DEFAULTS)
        transport_name = cfg.options.get("transport", cls.DEFAULT_TRANSPORT)
        transport_cls = _TRANSPORTS.get(transport_name)
        if transport_cls is None:
            raise InstrumentError(
                f"[devices.{cfg.key}] unknown transport {transport_name!r} "
                f"(expected one of {sorted(_TRANSPORTS)})"
            )
        # config options override driver defaults; unknown keys are rejected
        # by the transport constructor, surfacing typos at startup.
        for key, value in cfg.options.items():
            if key not in ("transport",):
                options[key] = value
        return cls(transport_cls(cfg.address, **options), cfg.key)

    def connect(self) -> None:
        """Open the transport and run the driver's ``_configure()`` hook."""
        with self.lock:
            if self.transport.is_open:
                return
            self.transport.open()
            try:
                self._configure()
            except Exception:
                self.transport.close()
                raise
            self.log.info("%s connected (%s)", self.name, self.transport.address)

    def close(self) -> None:
        with self.lock:
            if self.transport.is_open:
                self.transport.close()
                self.log.info("%s disconnected", self.name)

    def __enter__(self) -> Self:
        self.connect()
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    @property
    def connected(self) -> bool:
        return self.transport.is_open

    def _configure(self) -> None:
        """Post-open setup hook; default does nothing."""

    # ------------------------------------------------------------------- I/O

    def _io(self, operation):
        """Run one transport operation; on failure, reconnect once and retry.

        Ported from the old ``Device._visa_io_with_reconnect``: transport
        settings (baud, terminations) are constructor state on the transport,
        so a reopen restores them automatically, and ``_configure()`` re-runs
        the driver's post-open setup (the old ``_after_reconnect``).
        """
        with self.lock:
            if not self.transport.is_open:
                self.connect()
            try:
                return operation()
            # broad by design: any transport error (pyvisa, serial, OSError)
            # must trigger the one reconnect attempt
            except Exception as first_error:  # noqa: BLE001
                self.log.warning("%s I/O failed (%s); reconnecting once", self.name, first_error)
                try:
                    self.transport.close()
                    self.transport.open()
                    self._configure()
                    time.sleep(self.RECONNECT_SETTLE_S)
                    return operation()
                except Exception as second_error:
                    self.transport.close()
                    raise ConnectionLost(
                        f"{self.name}: I/O failed after reconnect: {second_error}"
                    ) from second_error

    def write(self, cmd: str) -> None:
        self._io(lambda: self.transport.write(cmd))

    def read(self) -> str:
        return self._io(self.transport.read)

    def query(self, cmd: str) -> str:
        return self._io(lambda: self.transport.query(cmd))

    def write_bytes(self, data: bytes) -> None:
        self._io(lambda: self.transport.write_bytes(data))

    def read_bytes(self, n: int = 1) -> bytes:
        return self._io(lambda: self.transport.read_bytes(n))

    def __repr__(self) -> str:
        state = "connected" if self.connected else "closed"
        return f"<{type(self).__name__} {self.name!r} @ {self.transport.address} ({state})>"
