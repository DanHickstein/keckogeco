"""Tests for the ORION laser driver: framing, checksums, unit conversions."""

import pytest

from keckogeco.config import DeviceConfig
from keckogeco.drivers.orion_laser import OrionLaser, _degC_to_ohm, _frame, _ohm_to_degC


@pytest.fixture
def laser():
    cfg = DeviceConfig(key="rio", driver="orion_laser", address="ASRL12::INSTR")
    inst = OrionLaser.from_config(cfg, sim=True)
    inst.connect()
    return inst


def test_frame_layout_and_checksum():
    pkt = _frame(0x1D)
    assert pkt[0] == 0xA9 and pkt[-1] == 0xA5
    assert pkt[2] == 7  # no payload
    assert sum(pkt[:-1]) & 0xFF == 0  # checksum balances the frame
    assert pkt[5] == 0x01  # read flag
    assert pkt[6] == 0x1D


def test_frame_write_flag_and_data():
    pkt = _frame(0x1E, data=(1450).to_bytes(2, "big"), read=False)
    assert pkt[5] == 0x02
    assert pkt[7:9] == (1450).to_bytes(2, "big")
    assert sum(pkt[:-1]) & 0xFF == 0


def test_steinhart_hart_roundtrip():
    # 9661 ohm is the documented default (~25.9 degC)
    temp = _ohm_to_degC(9661)
    assert temp == pytest.approx(25.9, abs=0.1)
    assert _degC_to_ohm(temp) == pytest.approx(9661, abs=2)


def test_identity_sim(laser):
    assert laser.product_id == "ORION-SIM"
    assert laser.serial_number == 806734
    assert laser.firmware_version == "1.2.0"


def test_monitors_sim(laser):
    assert laser.board_temperature_C == pytest.approx(20.0, abs=1.0)
    assert laser.photomonitor_V == pytest.approx(1.0, abs=0.01)


def test_diode_current_roundtrip(laser):
    assert laser.diode_current_mA(volatile=True) == pytest.approx(145.0)
    laser.set_diode_current_mA(150.0, volatile=True)
    assert laser.diode_current_mA(volatile=True) == pytest.approx(150.0)


def test_tec_setpoint_roundtrip(laser):
    assert laser.tec_setpoint_C() == pytest.approx(25.9, abs=0.1)
    laser.set_tec_setpoint_C(19.181, volatile=True)
    assert laser.tec_setpoint_C(volatile=True) == pytest.approx(19.18, abs=0.05)


def test_corrupt_frame_raises():
    from keckogeco.drivers.errors import ConnectionLost
    from keckogeco.drivers.transports import SimTransport

    def bad_responder(_request):
        return b"\xa9\x00\x08\x00\x00\x00\x00\x1d\xff\xa5"  # wrong checksum

    inst = OrionLaser(SimTransport({bytes: bad_responder}), "rio")
    inst.connect()
    # ResponseError inside _io triggers reconnect, then fails again -> ConnectionLost
    with pytest.raises(ConnectionLost, match="checksum|malformed"):
        inst.diode_current_mA()
