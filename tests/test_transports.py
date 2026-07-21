"""Tests for the transport layer (SimTransport, mostly — no hardware in CI)."""

import re

import pytest

from keckogeco.drivers.errors import NotConnected
from keckogeco.drivers.transports import SimTransport


def test_sim_exact_match():
    sim = SimTransport({":SENS:POW:OUT:CH1?": "150.0"})
    sim.open()
    assert sim.query(":SENS:POW:OUT:CH1?") == "150.0"
    assert sim.query("SOMETHING:ELSE?") == "0"


def test_sim_regex_match_with_callable():
    sim = SimTransport({re.compile(r"MEAS(\d)\?"): lambda m: f"chan{m.group(1)}"})
    sim.open()
    assert sim.query("MEAS2?") == "chan2"


def test_sim_records_sent_commands():
    sim = SimTransport()
    sim.open()
    sim.write("A")
    sim.query("B?")
    assert sim.sent == ["A", "B?"]


def test_sim_write_then_read():
    sim = SimTransport({"*IDN?": "FAKE,INSTRUMENT,0,1"})
    sim.open()
    sim.write("*IDN?")
    assert sim.read() == "FAKE,INSTRUMENT,0,1"


def test_sim_requires_open():
    sim = SimTransport()
    with pytest.raises(NotConnected):
        sim.query("*IDN?")
    sim.open()
    assert sim.is_open
    sim.close()
    assert not sim.is_open


def test_socket_transport_rejects_bad_address():
    from keckogeco.drivers.errors import InstrumentError
    from keckogeco.drivers.transports import SocketTransport

    with pytest.raises(InstrumentError, match="host:port"):
        SocketTransport("no-port-here").open()


class _FakeSerialException(Exception):
    pass


def _fake_serial_module(events):
    """Stand-in pyserial that records the line state at open time."""
    import types

    class FakeSerial:
        def __init__(self, port=None, **kwargs):
            self._dtr = True  # pyserial default: lines asserted at open
            self._rts = True
            self.port = port
            self.is_open = False
            self.timeout = kwargs.get("timeout")
            self.write_timeout = kwargs.get("write_timeout")
            if port is not None:
                self.open()

        def send_break(self, duration=0.25):
            events.append(("break", duration))

        def reset_input_buffer(self):
            events.append("flush_in")

        def reset_output_buffer(self):
            events.append("flush_out")

        @property
        def dtr(self):
            return self._dtr

        @dtr.setter
        def dtr(self, value):
            self._dtr = value

        @property
        def rts(self):
            return self._rts

        @rts.setter
        def rts(self, value):
            self._rts = value

        def open(self):
            self.is_open = True
            events.append(("open", self._dtr, self._rts))

        def close(self):
            self.is_open = False

    module = types.ModuleType("serial")
    module.Serial = FakeSerial
    module.SerialException = _FakeSerialException
    return module


def test_serial_transport_dtr_suppressed_before_open(monkeypatch):
    """dtr/rts options must be applied BEFORE the OS handle opens: a
    default open asserts DTR, and on an Arduino that edge resets the MCU
    (rebooting the Pritel interlock into its tripped state, rack-probed
    2026-07-20). Setting the lines after open would be too late."""
    import sys

    from keckogeco.drivers.transports import SerialTransport

    events = []
    monkeypatch.setitem(sys.modules, "serial", _fake_serial_module(events))

    suppressed = SerialTransport("COM4", dtr=False, rts=False)
    suppressed.open()
    assert events == [("open", False, False)]

    events.clear()
    default = SerialTransport("COM4")
    assert default.dtr is None and default.rts is None
    default.open()
    assert events == [("open", True, True)]


def test_serial_clear_flushes_only_by_default(monkeypatch):
    import sys

    from keckogeco.drivers.transports import SerialTransport

    events = []
    monkeypatch.setitem(sys.modules, "serial", _fake_serial_module(events))

    plain = SerialTransport("COM8")
    plain.open()
    events.clear()
    plain.clear()
    assert events == ["flush_in", "flush_out"]


def test_serial_clear_sends_break_when_enabled(monkeypatch):
    """break_on_clear turns clear() into the SIM900's RS-232 Device Clear:
    flush, then a <break> long enough for one character frame at any
    DIP-selectable baud, then flush the input again."""
    import sys

    from keckogeco.drivers.transports import SerialTransport

    events = []
    monkeypatch.setitem(sys.modules, "serial", _fake_serial_module(events))

    sim900 = SerialTransport("COM23", break_on_clear=True)
    sim900.open()
    events.clear()
    sim900.clear()
    assert events == ["flush_in", "flush_out", ("break", SerialTransport.BREAK_S), "flush_in"]


def test_serial_timeout_probing_api(monkeypatch):
    """SerialTransport exposes the same timeout_ms/set_timeout_ms pair as
    VisaTransport, so SIM900.module_inventory can shorten the timeout to
    sweep empty slots on either transport."""
    import sys

    from keckogeco.drivers.transports import SerialTransport

    events = []
    monkeypatch.setitem(sys.modules, "serial", _fake_serial_module(events))

    transport = SerialTransport("COM23", timeout_s=25.0)
    transport.open()
    assert transport.timeout_ms == 25_000
    transport.set_timeout_ms(1500)
    assert transport.timeout_s == pytest.approx(1.5)
    assert transport._port.timeout == pytest.approx(1.5)
    transport.set_timeout_ms(25_000)
    assert transport._port.timeout == pytest.approx(25.0)


def test_visa_transports_have_private_locks():
    """Every VISA transport gets its own I/O lock. The per-GPIB-board
    shared lock (2026-07-16, ni4882 AVs under concurrent multi-instrument
    polling) was removed once the OSA became the bus's only instrument —
    its driver RLock already serializes board I/O. If a second GPIB
    instrument ever returns, restore the per-board lock."""
    from keckogeco.drivers.transports import VisaTransport

    osa = VisaTransport("GPIB0::30::INSTR")
    other = VisaTransport("GPIB0::2::INSTR")
    usb = VisaTransport("USB0::0x0957::0x2807::MY62003852::INSTR")

    assert osa._io_lock is not other._io_lock
    assert osa._io_lock is not usb._io_lock
