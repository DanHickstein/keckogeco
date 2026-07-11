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
