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

    def open(self) -> None:
        import pyvisa

        if self._resource is not None:
            return
        rm = pyvisa.ResourceManager()
        try:
            resource = rm.open_resource(self.address)
        except pyvisa.errors.Error as exc:
            raise InstrumentError(f"Could not open VISA resource {self.address}: {exc}") from exc
        resource.timeout = self.timeout_ms
        for key, value in self.attrs.items():
            setattr(resource, key, value)
        self._resource = resource

    def close(self) -> None:
        if self._resource is not None:
            try:
                self._resource.close()
            except Exception as exc:  # noqa: BLE001 - closing must never raise
                log.debug("Ignoring error while closing %s: %s", self.address, exc)
            self._resource = None

    @property
    def is_open(self) -> bool:
        return self._resource is not None

    def _require_open(self):
        if self._resource is None:
            raise NotConnected(f"VISA resource {self.address} is not open")
        return self._resource

    def write(self, cmd: str) -> None:
        self._require_open().write(cmd)

    def read(self) -> str:
        return self._require_open().read()

    def query(self, cmd: str) -> str:
        return self._require_open().query(cmd)

    def write_bytes(self, data: bytes) -> None:
        self._require_open().write_raw(data)

    def read_bytes(self, n: int = 1) -> bytes:
        return bytes(self._require_open().read_bytes(n))


class SerialTransport:
    """pyserial-backed transport for plain COM-port devices.

    Used by drivers whose devices are not VISA-friendly (Arduino relay,
    TC-720, hk_shutter) or that need binary framing.
    """

    def __init__(
        self,
        address: str,
        baud_rate: int = 9600,
        timeout_s: float = 1.0,
        terminator: str = "\r\n",
    ):
        self.address = address
        self.baud_rate = baud_rate
        self.timeout_s = timeout_s
        self.terminator = terminator
        self._port = None

    def open(self) -> None:
        import serial

        if self._port is not None:
            return
        try:
            self._port = serial.Serial(
                port=self.address,
                baudrate=self.baud_rate,
                timeout=self.timeout_s,
                write_timeout=self.timeout_s,
            )
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
        if self._pending_bytes:
            data, self._pending_bytes = self._pending_bytes[:n], self._pending_bytes[n:]
            return data
        return b"\x00" * n
