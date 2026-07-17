"""Tests for the SIM900 mainframe + SIM960/SIM928 module drivers."""

import pytest

from keckogeco.config import DeviceConfig
from keckogeco.drivers.srs_sim900 import SIM900


@pytest.fixture
def srs():
    cfg = DeviceConfig(key="srs", driver="srs_sim900", address="ASRL21::INSTR")
    inst = SIM900.from_config(cfg, sim=True)
    inst.connect()
    return inst


def test_slot_routing_sends_conn(srs):
    servo = srs.sim960(5)
    _ = servo.setpoint_V
    conn_cmds = [c for c in srs.transport.sent if c.startswith("CONN")]
    assert conn_cmds and conn_cmds[-1] == 'CONN 5, "xyx"'


def test_slots_have_independent_state(srs):
    servo5 = srs.sim960(5)
    servo3 = srs.sim960(3)
    servo5.setpoint_V = 1.5
    servo3.setpoint_V = -0.25
    assert servo5.setpoint_V == pytest.approx(1.5)
    assert servo3.setpoint_V == pytest.approx(-0.25)


def test_pid_terms_roundtrip(srs):
    servo = srs.sim960(5)
    servo.proportional_gain = -2.5
    servo.integral_gain = 500
    servo.proportional_on = True
    assert servo.proportional_gain == pytest.approx(-2.5)
    assert servo.integral_gain == pytest.approx(500)
    assert servo.proportional_on is True


def test_gain_limits(srs):
    servo = srs.sim960(5)
    with pytest.raises(ValueError, match="above maximum"):
        servo.proportional_gain = 2000
    with pytest.raises(ValueError, match="below minimum"):
        servo.integral_gain = 1e-3


def test_output_mode_and_setpoint_source(srs):
    servo = srs.sim960(5)
    assert servo.output_mode == "MAN"
    servo.output_mode = "PID"
    assert servo.output_mode == "PID"
    servo.setpoint_source = "EXT"
    assert servo.setpoint_source == "EXT"
    with pytest.raises(ValueError):
        servo.output_mode = "AUTO"


def test_manual_output_limits_and_ramp(srs):
    servo = srs.sim960(5)
    servo.manual_output_min, servo.manual_output_max = -4.0, 4.0
    with pytest.raises(ValueError, match="outside"):
        servo.manual_output_V = 5.0
    servo.manual_output_ramp = 0.5
    servo.manual_output_V = 2.0
    mouts = [c for c in srs.transport.sent if c.startswith("MOUT") and not c.endswith("?")]
    assert len(mouts) >= 4  # ramped in steps
    assert servo.manual_output_V == pytest.approx(2.0)


def test_setpoint_resolution_rounding(srs):
    servo = srs.sim960(5)
    servo.setpoint_V = 1.23456
    assert servo.setpoint_V == pytest.approx(1.235)  # 1 mV resolution


def test_slot_write_syncs_before_returning(srs):
    """Every module write is followed by a query on the same connection:
    the mainframe forwards commands to the module over a slow internal
    link, and the next operation's device clear flushes that pipe — an
    unsynced write could be silently discarded (live 2026-07-17: SETP
    while locked never landed because PUT /im read state right back)."""
    servo = srs.sim960(3)
    servo.setpoint_V = 2.45
    sent = srs.transport.sent
    setp = max(i for i, c in enumerate(sent) if c.startswith("SETP") and not c.endswith("?"))
    assert "*IDN?" in sent[setp + 1 :]  # sync query after the write, before any clear


def test_module_inventory(srs):
    """The inventory IDNs every slot; sim rack = SIM960s in 3+5, SIM928
    in 2, everything else empty (no reply)."""
    inventory = srs.module_inventory()
    assert set(inventory) == set(range(1, 9))
    assert "SIM960" in inventory[3]
    assert "SIM960" in inventory[5]
    assert "SIM928" in inventory[2]
    assert all(inventory[slot] is None for slot in (1, 4, 6, 7, 8))


def test_sim928_voltage_and_output(srs):
    source = srs.sim928(2)
    source.voltage_V = 3.3
    assert source.voltage_V == pytest.approx(3.3)
    assert source.output_on is False
    source.output_on = True
    assert source.output_on is True
