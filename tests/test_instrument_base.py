"""Tests for the Instrument base class: lifecycle, reconnect, from_config."""

import pytest

from keckogeco.config import DeviceConfig
from keckogeco.drivers.base import Instrument
from keckogeco.drivers.errors import ConnectionLost, InstrumentError
from keckogeco.drivers.transports import SimTransport


class FlakyTransport(SimTransport):
    """Fails the first `fail_count` operations, then behaves normally."""

    def __init__(self, fail_count=1, **kwargs):
        super().__init__(**kwargs)
        self.fail_count = fail_count
        self.open_count = 0

    def open(self):
        super().open()
        self.open_count += 1

    def query(self, cmd):
        if self.fail_count > 0:
            self.fail_count -= 1
            raise OSError("simulated I/O failure")
        return super().query(cmd)


class Fake(Instrument):
    RECONNECT_SETTLE_S = 0.0
    SIM_RESPONSES = {"*IDN?": "FAKE,MODEL,0,1"}
    configure_calls = 0

    def _configure(self):
        type(self).configure_calls += 1


def test_context_manager_lifecycle():
    inst = Fake(SimTransport({"*IDN?": "x"}))
    assert not inst.connected
    with inst:
        assert inst.connected
    assert not inst.connected


def test_reconnect_once_then_succeed():
    Fake.configure_calls = 0
    transport = FlakyTransport(fail_count=1, responses={"Q?": "42"})
    inst = Fake(transport)
    inst.connect()
    assert inst.query("Q?") == "42"
    assert transport.open_count == 2  # initial + one reconnect
    assert Fake.configure_calls == 2  # _configure re-ran after reconnect


def test_reconnect_fails_raises_connection_lost():
    transport = FlakyTransport(fail_count=99)
    inst = Fake(transport)
    inst.connect()
    with pytest.raises(ConnectionLost):
        inst.query("Q?")
    assert not inst.connected  # left closed after giving up


def test_from_config_sim_mode_uses_sim_responses():
    cfg = DeviceConfig(key="fake1", driver="fake", address="ASRL99::INSTR")
    inst = Fake.from_config(cfg, sim=True)
    with inst:
        assert inst.query("*IDN?") == "FAKE,MODEL,0,1"
    assert inst.name == "fake1"


def test_from_config_unknown_transport_rejected():
    cfg = DeviceConfig(
        key="bad", driver="fake", address="X", options={"transport": "carrier-pigeon"}
    )
    with pytest.raises(InstrumentError, match="carrier-pigeon"):
        Fake.from_config(cfg)


def test_from_config_builds_serial_transport():
    from keckogeco.drivers.transports import SerialTransport

    cfg = DeviceConfig(
        key="relay",
        driver="arduino_relay",
        address="COM3",
        options={"transport": "serial", "baud_rate": 115200},
    )
    inst = Fake.from_config(cfg)
    assert isinstance(inst.transport, SerialTransport)
    assert inst.transport.baud_rate == 115200
    assert inst.transport.address == "COM3"
