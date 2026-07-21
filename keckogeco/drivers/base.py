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
from collections.abc import Callable
from typing import ClassVar, Self

from ..config import DeviceConfig
from .errors import ConnectionLost, InstrumentError
from .transports import SerialTransport, SimTransport, SocketTransport, Transport, VisaTransport

__all__ = ["Instrument"]

#: exception-text markers of a crash in the native I/O layer (ctypes turns
#: a Windows SEH fault inside a driver DLL into OSError("exception: access
#: violation reading 0x...")). After one of these the layer's internal
#: state is undefined: the 2026-07-17 ni4882 access violation survived the
#: except clause, but the reconnect's close/reopen then hung forever inside
#: the crashed DLL — holding locks that wedged the poller and starved the
#: whole server. These errors are never retried. Still load-bearing after
#: the GPIB thin-out: the OSA (GPIB) and the Pendulum + Keysight FGs
#: (USB-TMC) all run through the same NI-VISA/ni4882 native stack.
_NATIVE_CRASH_MARKERS = ("access violation", "stack overflow")


def _is_native_crash(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _NATIVE_CRASH_MARKERS)


# DeviceConfig "transport" option value -> transport class. Drivers pick a
# default via DEFAULT_TRANSPORT; a config block can override with
# transport = "serial" etc.
_TRANSPORTS = {
    "visa": VisaTransport,
    "serial": SerialTransport,
    "socket": SocketTransport,
}

# Config-block keys consumed by the orchestration layer (LFCController),
# not by the driver or transport.
CONTROLLER_KEYS = frozenset({"channel", "im_slot"})

# Bookkeeping keys that discovery writes into [devices.*] blocks; they
# identify the device across COM renumbering and are not transport kwargs.
DISCOVERY_KEYS = frozenset(
    {
        "device",
        "usb_serial",
        "vid_pid",
        "adapter",
        "probe",
        "match_token",
        "confidence",
        "passive",
        "found_on",
        "verified_on",
        "missing_since",
        "note",
    }
)


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
    #: reply for unmatched sim commands (e.g. the OZ VOA answers "Done")
    SIM_DEFAULT: ClassVar[str] = "0"
    #: config-block option keys consumed by the driver's __init__ rather
    #: than the transport (e.g. ("model",) for the Instek supplies)
    DRIVER_OPTIONS: ClassVar[tuple[str, ...]] = ()

    #: seconds to wait between reopening the transport and retrying I/O
    RECONNECT_SETTLE_S: ClassVar[float] = 0.2

    def __init__(self, transport: Transport, name: str = ""):
        self.transport = transport
        self.name = name or type(self).__name__
        self.lock = threading.RLock()
        self.log = logging.getLogger(f"keckogeco.drivers.{self.name}")
        #: set to the error text after a native I/O-layer crash; all
        #: further I/O on this instrument is refused until the process
        #: restarts (see _NATIVE_CRASH_MARKERS)
        self._poisoned: str | None = None

    # ------------------------------------------------------------- lifecycle

    @classmethod
    def sim_responses(cls) -> dict:
        """Sim-mode response table; override for per-instance stateful tables.

        The default returns a copy of ``SIM_RESPONSES``. Drivers whose
        setters verify the device state (write-then-read-back loops) should
        override this to return a table whose callables share a small state
        dict, so a sim write is visible to the following read.
        """
        return dict(cls.SIM_RESPONSES)

    @classmethod
    def transport_defaults(cls, cfg: DeviceConfig) -> dict:
        """Transport kwargs for this device; may depend on config options
        (e.g. the Instek baud rate differs by model)."""
        del cfg
        return dict(cls.TRANSPORT_DEFAULTS)

    @classmethod
    def from_config(cls, cfg: DeviceConfig, sim: bool = False) -> Self:
        """Build the instrument (and its transport) from a config block."""
        driver_kwargs = {k: cfg.options[k] for k in cls.DRIVER_OPTIONS if k in cfg.options}
        if sim:
            transport = SimTransport(
                cls.sim_responses(), default=cls.SIM_DEFAULT, address=f"SIM::{cfg.address}"
            )
            return cls(transport, cfg.key, **driver_kwargs)
        options = cls.transport_defaults(cfg)
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
            if (
                key not in ("transport", *cls.DRIVER_OPTIONS)
                and key not in DISCOVERY_KEYS
                and key not in CONTROLLER_KEYS
            ):
                options[key] = value
        return cls(transport_cls(cfg.address, **options), cfg.key, **driver_kwargs)

    def connect(self) -> None:
        """Open the transport and run the driver's ``_configure()`` hook."""
        with self.lock:
            if self._poisoned is not None:
                raise ConnectionLost(
                    f"{self.name}: disabled after a native I/O crash "
                    f"({self._poisoned}); restart the server to recover"
                )
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

    def _io(self, operation, what: str | Callable[[], str] = ""):
        """Run one transport operation; on failure, reconnect once and retry.

        Ported from the old ``Device._visa_io_with_reconnect``: transport
        settings (baud, terminations) are constructor state on the transport,
        so a reopen restores them automatically, and ``_configure()`` re-runs
        the driver's post-open setup (the old ``_after_reconnect``).

        ``what`` names the operation in failure logs (e.g. the command
        string). A callable is evaluated at failure time, so multi-step
        operations can report which step failed.
        """
        with self.lock:
            if self._poisoned is not None:
                raise ConnectionLost(
                    f"{self.name}: disabled after a native I/O crash "
                    f"({self._poisoned}); restart the server to recover"
                )
            if not self.transport.is_open:
                self.connect()
            try:
                return operation()
            # broad by design: any transport error (pyvisa, serial, OSError)
            # must trigger the one reconnect attempt
            except Exception as first_error:  # noqa: BLE001
                desc = (what() if callable(what) else what) or "I/O"
                if _is_native_crash(first_error):
                    # the DLL's internal state (and any locks it holds) is
                    # undefined — one more call through it can hang forever
                    # with our driver lock held. Fail fast and stay failed.
                    self._poisoned = str(first_error)
                    self.log.critical(
                        "%s: %s crashed in the native I/O layer (%s); disabling "
                        "this device — restart the server to recover it",
                        self.name,
                        desc,
                        first_error,
                    )
                    raise ConnectionLost(
                        f"{self.name}: {desc} crashed the native I/O layer: {first_error}"
                    ) from first_error
                self.log.warning(
                    "%s: %s failed (%s); reconnecting once", self.name, desc, first_error
                )
                try:
                    self.transport.close()
                    self.transport.open()
                    self._configure()
                    time.sleep(self.RECONNECT_SETTLE_S)
                    return operation()
                except Exception as second_error:
                    self.transport.close()
                    raise ConnectionLost(
                        f"{self.name}: {desc} failed after reconnect: {second_error}"
                    ) from second_error

    def write(self, cmd: str) -> None:
        self._io(lambda: self.transport.write(cmd), what=f"write {cmd!r}")

    def read(self) -> str:
        return self._io(self.transport.read, what="read")

    def query(self, cmd: str) -> str:
        return self._io(lambda: self.transport.query(cmd), what=f"query {cmd!r}")

    def write_bytes(self, data: bytes) -> None:
        self._io(lambda: self.transport.write_bytes(data), what="write_bytes")

    def read_bytes(self, n: int = 1) -> bytes:
        return self._io(lambda: self.transport.read_bytes(n), what="read_bytes")

    def __repr__(self) -> str:
        state = "connected" if self.connected else "closed"
        return f"<{type(self).__name__} {self.name!r} @ {self.transport.address} ({state})>"
