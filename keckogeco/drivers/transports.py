"""Transport layer: how bytes get to and from an instrument.

Drivers contain protocol logic only (what commands to send, how to parse
replies); a :class:`Transport` carries them over VISA, a raw serial port, a
TCP socket, or a canned-response simulation. This is what lets every driver
run unmodified in ``--sim`` mode and lets the test suite exercise command
formatting without hardware.

All transports share the same small API::

    open() / close() / is_open
    write(cmd: str)          # text command, termination added by transport
    read() -> str
    query(cmd: str) -> str   # write + read
    write_bytes(data: bytes) # binary protocols (TC-720, Agiltron, ...)
    read_bytes(n) -> bytes
"""

from __future__ import annotations

import logging
import re
import socket
import threading
import time
from typing import Protocol, runtime_checkable

from .errors import InstrumentError, NotConnected

__all__ = [
    "SerialTransport",
    "SimTransport",
    "SocketTransport",
    "Transport",
    "VisaTransport",
]

log = logging.getLogger(__name__)


@runtime_checkable
class Transport(Protocol):
    """Minimal byte/str pipe to one instrument."""

    address: str

    def open(self) -> None: ...

    def close(self) -> None: ...

    @property
    def is_open(self) -> bool: ...

    def write(self, cmd: str) -> None: ...

    def read(self) -> str: ...

    def query(self, cmd: str) -> str: ...

    def write_bytes(self, data: bytes) -> None: ...

    def read_bytes(self, n: int = 1) -> bytes: ...

    def read_available(self) -> bytes: ...

    def clear(self) -> None:
        """Device-clear / flush; optional, default no-op."""


class VisaTransport:
    """pyvisa-backed transport (GPIB, USB-TMC, and ASRL serial resources).

    Parameters
    ----------
    address : str
        VISA resource string, e.g. ``"GPIB0::30::INSTR"`` or
        ``"ASRL13::INSTR"``.
    timeout_ms : int
        I/O timeout in milliseconds.
    **attrs
        Extra pyvisa resource attributes applied after opening
        (``baud_rate``, ``read_termination``, ``write_termination``, ...).
        Re-applied on every open, so a reconnect restores them — this
        replaces the old ``Device._snapshot_io_attrs`` dance.
    """

    def __init__(self, address: str, timeout_ms: int = 2000, **attrs):
        self.address = address
        self.timeout_ms = timeout_ms
        self.attrs = attrs
        self._resource = None
        # Private per-resource lock. GPIB instruments used to share one
        # lock per board: concurrent multi-instrument polling crashed
        # ni4882 with native access violations (2026-07-16), and the
        # shared lock let one wedged instrument starve its bus-mates
        # (2026-07-20). With the SIM900 and Pendulum moved off GPIB the
        # OSA is alone on the bus and its driver RLock already serializes
        # board I/O — but if a second GPIB instrument ever returns, bring
        # the per-board lock back (git: sim900-rs232 branch removed it).
        self._io_lock = threading.RLock()

    def open(self) -> None:
        import pyvisa

        if self._resource is not None:
            return
        with self._io_lock:
            rm = pyvisa.ResourceManager()
            try:
                resource = rm.open_resource(self.address)
            except pyvisa.errors.Error as exc:
                raise InstrumentError(
                    f"Could not open VISA resource {self.address}: {exc}"
                ) from exc
            resource.timeout = self.timeout_ms
            for key, value in self.attrs.items():
                setattr(resource, key, value)
            self._resource = resource

    def close(self) -> None:
        if self._resource is not None:
            with self._io_lock:
                try:
                    self._resource.close()
                except Exception as exc:  # noqa: BLE001 - closing must never raise
                    log.debug("Ignoring error while closing %s: %s", self.address, exc)
                self._resource = None

    @property
    def is_open(self) -> bool:
        return self._resource is not None

    def set_timeout_ms(self, timeout_ms: int) -> None:
        """Change the I/O timeout on the open resource (and future opens).
        Used to probe for absent hardware — e.g. empty SIM900 slots —
        without paying the instrument's full reply timeout per miss."""
        self.timeout_ms = int(timeout_ms)
        if self._resource is not None:
            self._resource.timeout = self.timeout_ms

    def _require_open(self):
        if self._resource is None:
            raise NotConnected(f"VISA resource {self.address} is not open")
        return self._resource

    def write(self, cmd: str) -> None:
        with self._io_lock:
            self._require_open().write(cmd)

    def read(self) -> str:
        with self._io_lock:
            return self._require_open().read()

    def query(self, cmd: str) -> str:
        with self._io_lock:
            return self._require_open().query(cmd)

    def write_bytes(self, data: bytes) -> None:
        with self._io_lock:
            self._require_open().write_raw(data)

    #: viRead chunk size for raw reads (matches pyvisa's default)
    _READ_CHUNK = 20 * 1024

    def read_bytes(self, n: int = 1) -> bytes:
        """Read raw bytes: up to ``n``, stopping early at end of message.

        Deliberately NOT ``resource.read_bytes(n)`` — pyvisa's contract
        there is "read exactly n bytes": it keeps issuing reads past the
        END indicator and times out when a message is shorter than ``n``
        (this hung every OSA REAL,32 trace pull, 2026-07-21). The manual
        loop stops as soon as viRead reports anything but max-count-read.
        The termchar is disabled for the duration so a 0x0A byte inside a
        binary payload cannot end the read early.
        """
        from pyvisa import constants

        with self._io_lock:
            resource = self._require_open()
            termchar_en = resource.get_visa_attribute(constants.VI_ATTR_TERMCHAR_EN)
            resource.set_visa_attribute(constants.VI_ATTR_TERMCHAR_EN, False)
            try:
                chunks: list[bytes] = []
                remaining = n
                while remaining > 0:
                    chunk, status = resource.visalib.read(
                        resource.session, min(remaining, self._READ_CHUNK)
                    )
                    chunks.append(chunk)
                    remaining -= len(chunk)
                    if status != constants.StatusCode.success_max_count_read:
                        break  # END (EOI) — the message is complete
                return b"".join(chunks)
            finally:
                resource.set_visa_attribute(constants.VI_ATTR_TERMCHAR_EN, termchar_en)

    def read_available(self) -> bytes:
        with self._io_lock:
            resource = self._require_open()
            pending = getattr(resource, "bytes_in_buffer", 0)
            return bytes(resource.read_bytes(pending)) if pending else b""

    def clear(self) -> None:
        """VISA device clear (used by the SIM900 to escape a module link)."""
        with self._io_lock:
            self._require_open().clear()


class SerialTransport:
    """pyserial-backed transport for plain COM-port devices.

    Used by drivers whose devices are not VISA-friendly (Arduino relay,
    TC-720, hk_shutter) or that need binary framing.

    ``dtr``/``rts`` pin the modem-control lines to a fixed state *before*
    the OS opens the port. A default open asserts DTR, and on an Arduino
    that edge is capacitor-coupled to the MCU reset pin — every server
    start rebooted the interlock firmware into its latched-tripped boot
    state (rack-probed 2026-07-20). ``None`` keeps pyserial's default
    (lines asserted at open), which some handshaking devices need.

    ``break_on_clear`` makes :meth:`clear` transmit a serial <break> in
    addition to flushing the OS buffers, for instruments that define the
    break signal as an out-of-band Device Clear (the SRS SIM900 uses it
    to escape a CONN'd module stream, mirroring the GPIB device clear).
    """

    #: <break> hold time: must span one full character frame at the
    #: slowest supported rate (8.3 ms at 1200 baud), rack-verified 50 ms
    BREAK_S = 0.05
    #: pause after the break so the instrument finishes its reset before
    #: the next command goes out
    BREAK_SETTLE_S = 0.05

    def __init__(
        self,
        address: str,
        baud_rate: int = 9600,
        timeout_s: float = 1.0,
        terminator: str = "\r\n",
        dtr: bool | None = None,
        rts: bool | None = None,
        break_on_clear: bool = False,
    ):
        self.address = address
        self.baud_rate = baud_rate
        self.timeout_s = timeout_s
        self.terminator = terminator
        self.dtr = dtr
        self.rts = rts
        self.break_on_clear = break_on_clear
        self._port = None

    def open(self) -> None:
        import serial

        if self._port is not None:
            return
        try:
            port = serial.Serial(
                baudrate=self.baud_rate,
                timeout=self.timeout_s,
                write_timeout=self.timeout_s,
            )
            # line states must be set before .open() — setting them after
            # would be too late, the open itself produces the DTR edge
            if self.dtr is not None:
                port.dtr = self.dtr
            if self.rts is not None:
                port.rts = self.rts
            port.port = self.address
            port.open()
            self._port = port
        except serial.SerialException as exc:
            raise InstrumentError(f"Could not open serial port {self.address}: {exc}") from exc

    def close(self) -> None:
        if self._port is not None:
            try:
                self._port.close()
            except Exception as exc:  # noqa: BLE001 - closing must never raise
                log.debug("Ignoring error while closing %s: %s", self.address, exc)
            self._port = None

    @property
    def is_open(self) -> bool:
        return self._port is not None

    @property
    def timeout_ms(self) -> int:
        return int(self.timeout_s * 1000)

    def set_timeout_ms(self, timeout_ms: int) -> None:
        """Change the I/O timeout on the open port (and future opens).
        Same probing API as :meth:`VisaTransport.set_timeout_ms` — the
        SIM900 driver shortens it to sweep empty mainframe slots."""
        self.timeout_s = timeout_ms / 1000
        if self._port is not None:
            self._port.timeout = self.timeout_s
            self._port.write_timeout = self.timeout_s

    def _require_open(self):
        if self._port is None:
            raise NotConnected(f"Serial port {self.address} is not open")
        return self._port

    def write(self, cmd: str) -> None:
        self._require_open().write((cmd + self.terminator).encode("ascii"))

    def read(self) -> str:
        raw = self._require_open().read_until(self.terminator.encode("ascii"))
        return raw.decode("ascii", errors="replace").strip()

    def query(self, cmd: str) -> str:
        port = self._require_open()
        port.reset_input_buffer()
        self.write(cmd)
        return self.read()

    def write_bytes(self, data: bytes) -> None:
        self._require_open().write(data)

    def read_bytes(self, n: int = 1) -> bytes:
        return self._require_open().read(n)

    def read_available(self) -> bytes:
        port = self._require_open()
        return port.read(port.in_waiting) if port.in_waiting else b""

    def clear(self) -> None:
        port = self._require_open()
        port.reset_input_buffer()
        port.reset_output_buffer()
        if self.break_on_clear:
            port.send_break(self.BREAK_S)
            time.sleep(self.BREAK_SETTLE_S)
            # the break may flush a partial reply out of the device
            port.reset_input_buffer()


class SocketTransport:
    """Line-oriented TCP transport (Red Pitaya SCPI server)."""

    def __init__(self, address: str, timeout_s: float = 2.0, terminator: str = "\r\n"):
        # address is "host:port"
        self.address = address
        self.timeout_s = timeout_s
        self.terminator = terminator
        self._sock: socket.socket | None = None
        self._buffer = b""

    def open(self) -> None:
        if self._sock is not None:
            return
        host, _, port = self.address.partition(":")
        if not port:
            raise InstrumentError(f"Socket address must be host:port, got {self.address!r}")
        try:
            self._sock = socket.create_connection((host, int(port)), timeout=self.timeout_s)
        except OSError as exc:
            raise InstrumentError(f"Could not connect to {self.address}: {exc}") from exc

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception as exc:  # noqa: BLE001 - closing must never raise
                log.debug("Ignoring error while closing %s: %s", self.address, exc)
            self._sock = None
            self._buffer = b""

    @property
    def is_open(self) -> bool:
        return self._sock is not None

    def _require_open(self) -> socket.socket:
        if self._sock is None:
            raise NotConnected(f"Socket {self.address} is not open")
        return self._sock

    def write(self, cmd: str) -> None:
        self._require_open().sendall((cmd + self.terminator).encode("ascii"))

    def read(self) -> str:
        sock = self._require_open()
        term = self.terminator.encode("ascii")
        while term not in self._buffer:
            chunk = sock.recv(4096)
            if not chunk:
                raise InstrumentError(f"Connection to {self.address} closed by peer")
            self._buffer += chunk
        line, _, self._buffer = self._buffer.partition(term)
        return line.decode("ascii", errors="replace").strip()

    def query(self, cmd: str) -> str:
        self.write(cmd)
        return self.read()

    def write_bytes(self, data: bytes) -> None:
        self._require_open().sendall(data)

    def read_bytes(self, n: int = 1) -> bytes:
        sock = self._require_open()
        while len(self._buffer) < n:
            chunk = sock.recv(4096)
            if not chunk:
                break
            self._buffer += chunk
        data, self._buffer = self._buffer[:n], self._buffer[n:]
        return data

    def read_available(self) -> bytes:
        data, self._buffer = self._buffer, b""
        return data

    def clear(self) -> None:
        self._buffer = b""


class SimTransport:
    """Canned-response transport for ``--sim`` mode and unit tests.

    Deliberately minimal — no instrument behavior is modeled. Two features
    only:

    * ``responses`` maps a command (exact string or compiled regex) to a
      reply; unmatched queries get ``default``.
    * set-commands are remembered: ``sent`` records every command, and a
      driver's ``SIM_RESPONSES`` can use regex entries whose replies are
      callables to echo stored values back if a panel needs it.

    Parameters
    ----------
    responses : dict
        ``{command: reply}`` where command is a ``str`` or ``re.Pattern``
        and reply is a ``str`` or ``callable(match | cmd) -> str``.
        Binary protocols use the reserved key ``bytes`` whose value is a
        ``callable(data: bytes) -> bytes`` producing the framed reply for a
        raw write.
    default : str
        Reply for any query with no matching entry.
    """

    def __init__(self, responses: dict | None = None, default: str = "0", address: str = "SIM"):
        self.address = address
        self.responses = dict(responses or {})
        self.bytes_responder = self.responses.pop(bytes, None)
        self.default = default
        self.sent: list[str] = []
        self._is_open = False
        self._pending_reply: str | None = None
        self._pending_bytes = b""

    def open(self) -> None:
        self._is_open = True

    def close(self) -> None:
        self._is_open = False

    @property
    def is_open(self) -> bool:
        return self._is_open

    def _reply_for(self, cmd: str) -> str:
        for key, reply in self.responses.items():
            if isinstance(key, re.Pattern):
                match = key.match(cmd)
                if match:
                    return reply(match) if callable(reply) else reply
            elif key == cmd:
                return reply(cmd) if callable(reply) else reply
        return self.default

    def write(self, cmd: str) -> None:
        if not self._is_open:
            raise NotConnected("SimTransport is not open")
        self.sent.append(cmd)
        self._pending_reply = self._reply_for(cmd)

    def read(self) -> str:
        if not self._is_open:
            raise NotConnected("SimTransport is not open")
        reply = self._pending_reply if self._pending_reply is not None else self.default
        self._pending_reply = None
        return reply

    def query(self, cmd: str) -> str:
        self.write(cmd)
        return self.read()

    def write_bytes(self, data: bytes) -> None:
        if not self._is_open:
            raise NotConnected("SimTransport is not open")
        self.sent.append(data.hex())
        if self.bytes_responder is not None:
            self._pending_bytes += self.bytes_responder(data) or b""

    def read_bytes(self, n: int = 1) -> bytes:
        if not self._pending_bytes and self._pending_reply is not None:
            # a text command whose reply is read as raw bytes (the OSA's
            # binary trace block): latin-1 round-trips byte values 0-255
            self._pending_bytes = self._pending_reply.encode("latin-1")
            self._pending_reply = None
        if self._pending_bytes:
            data, self._pending_bytes = self._pending_bytes[:n], self._pending_bytes[n:]
            return data
        return b"\x00" * n

    def read_available(self) -> bytes:
        """Return the pending text reply (as bytes) and/or pending bytes."""
        data = self._pending_bytes
        self._pending_bytes = b""
        if self._pending_reply is not None:
            data += self._pending_reply.encode()
            self._pending_reply = None
        return data

    def clear(self) -> None:
        self._pending_reply = None
        self._pending_bytes = b""
