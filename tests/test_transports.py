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


def test_gpib_transports_share_one_board_lock():
    """All VISA transports on one GPIB board share a single I/O lock:
    NI-488 crashed the server with native access violations under
    concurrent multi-instrument polling (2026-07-16), so calls into the
    NI layer are serialized per board. Other VISA resources (USB-TMC,
    ASRL) keep private locks."""
    from keckogeco.drivers.transports import VisaTransport

    srs = VisaTransport("GPIB0::2::INSTR")
    osa = VisaTransport("GPIB0::30::INSTR")
    cnt = VisaTransport("gpib0::10::INSTR")  # VISA is case-insensitive
    other_board = VisaTransport("GPIB1::5::INSTR")
    usb = VisaTransport("USB0::0x0957::0x2807::MY62003852::INSTR")
    asrl = VisaTransport("ASRL13::INSTR")

    assert srs._io_lock is osa._io_lock
    assert srs._io_lock is cnt._io_lock
    assert other_board._io_lock is not srs._io_lock
    assert usb._io_lock is not srs._io_lock
    assert asrl._io_lock is not srs._io_lock
    assert usb._io_lock is not asrl._io_lock
