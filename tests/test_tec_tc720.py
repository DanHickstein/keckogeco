"""Tests for the TC-720 TEC controller driver."""

import pytest

from keckogeco.config import DeviceConfig
from keckogeco.drivers.tec_tc720 import TC720, _build_message, _checksum, _hex_to_int, _int_to_hex


@pytest.fixture
def tec():
    cfg = DeviceConfig(key="tec_ppln", driver="tec_tc720", address="COM16")
    inst = TC720.from_config(cfg, sim=True)
    inst.connect()
    return inst


def test_hex_encoding_roundtrip():
    assert _int_to_hex(2500) == "09c4"
    assert _hex_to_int("09c4") == 2500
    assert _hex_to_int(_int_to_hex(-100)) == -100


def test_checksum_matches_vendor_example():
    # '*' + '01' + '0000' -> checksum of '010000'
    message = _build_message("01")
    assert message.startswith("*010000")
    assert message.endswith("\r")
    assert message[7:9] == _checksum("010000")


def test_read_temperature(tec):
    assert tec.temperature_C == pytest.approx(25.0)  # sim sits at setpoint
    assert tec.temperature2_C == pytest.approx(23.12)


def test_set_temperature_roundtrip(tec):
    tec.set_temperature_C(31.5)
    assert tec.setpoint_C == pytest.approx(31.5)
    assert tec.temperature_C == pytest.approx(31.5)


def test_set_temperature_requires_mode0(tec):
    tec.set_mode(1)
    with pytest.raises(RuntimeError, match="mode 0"):
        tec.set_temperature_C(25.0)
    tec.set_mode(0)
    tec.set_temperature_C(25.0)


def test_output_limits(tec):
    tec.set_output(100)
    assert tec.output == 100
    with pytest.raises(ValueError, match="outside"):
        tec.set_output(600)


def test_status(tec):
    status = tec.status()
    assert set(status) >= {"temperature_C", "setpoint_C", "output", "mode"}


def test_default_transport():
    cfg = DeviceConfig(key="tec_ppln", driver="tec_tc720", address="COM16")
    inst = TC720.from_config(cfg)
    from keckogeco.drivers.transports import SerialTransport

    assert isinstance(inst.transport, SerialTransport)
    assert inst.transport.baud_rate == 230_400
