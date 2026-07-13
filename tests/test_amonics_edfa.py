"""Tests for the Amonics EDFA driver against SimTransport."""

import pytest

from keckogeco.config import DeviceConfig
from keckogeco.drivers.amonics_edfa import AmonicsEDFA
from keckogeco.drivers.transports import SimTransport


@pytest.fixture
def edfa():
    cfg = DeviceConfig(key="edfa27", driver="amonics_edfa", address="ASRL13::INSTR")
    inst = AmonicsEDFA.from_config(cfg, sim=True)
    inst.connect()
    return inst


def test_identity_and_monitors(edfa):
    assert edfa.model == "AEDFA-SIM-30-B-FA"
    assert edfa.interlocked is False
    assert edfa.case_temperature_C == pytest.approx(35.5)
    assert edfa.output_power_mW() == pytest.approx(150.0)


def test_setpoint_roundtrip(edfa):
    assert edfa.set_setpoint(50, channel=1) == pytest.approx(50.0)
    assert edfa.setpoint(1) == pytest.approx(50.0)
    # the command actually sent used ACC mode and channel 1
    assert ":DRIV:ACC:CUR:CH1 50.0" in edfa.transport.sent


def test_setpoint_clamped_to_max(edfa):
    readback = edfa.set_setpoint(10_000, channel=1)
    assert readback == pytest.approx(1500.0)  # sim max


def test_channel_and_activation_sequence(edfa):
    assert edfa.channel_state(1) == "OFF"
    edfa.set_channel("on", channel=1)
    assert edfa.channel_state(1) == "ON"
    assert edfa.activation is False
    edfa.activate()
    assert edfa.activation is True
    edfa.deactivate()
    edfa.set_channel(0, channel=1)
    assert edfa.channel_state(1) == "OFF"


def test_activate_refused_when_channel_off(edfa):
    assert edfa.channel_state(1) == "OFF"
    with pytest.raises(RuntimeError, match="CH1 is OFF"):
        edfa.activate()


def test_switch_input_validation(edfa):
    with pytest.raises(ValueError, match="on"):
        edfa.set_channel("maybe")


def test_status_dict(edfa):
    status = edfa.status()
    assert status["mode"] == "ACC"
    assert status["channel_state"] == "OFF"
    assert set(status) >= {"interlocked", "activation", "output_power_mW", "setpoint"}


def test_sim_state_is_per_instance():
    """Two sim EDFAs must not share channel state."""
    cfg1 = DeviceConfig(key="edfa13", driver="amonics_edfa", address="A")
    cfg2 = DeviceConfig(key="edfa23", driver="amonics_edfa", address="B")
    a = AmonicsEDFA.from_config(cfg1, sim=True)
    b = AmonicsEDFA.from_config(cfg2, sim=True)
    a.connect()
    b.connect()
    a.set_channel("on")
    assert a.channel_state() == "ON"
    assert b.channel_state() == "OFF"


def test_mode_from_config_option_skips_query():
    """A configured mode is used as-is; the mode query is never sent."""
    cfg = DeviceConfig(
        key="edfa27", driver="amonics_edfa", address="A", options={"mode": "APC"}
    )
    inst = AmonicsEDFA.from_config(cfg, sim=True)
    inst.connect()
    assert inst.mode() == "APC"
    assert inst.set_setpoint(50) == pytest.approx(50.0)
    assert ":DRIV:APC:CUR:CH1 50.0" in inst.transport.sent
    assert not any(":MODE:SW:CH1?" in c for c in inst.transport.sent)


def test_mode_option_validated():
    with pytest.raises(ValueError, match="ACC"):
        AmonicsEDFA(SimTransport({}), "edfa-test", mode="AGC")


def test_mode_falls_back_to_acc_when_query_unanswered():
    """PM-13/PM-23 behavior: no answer to :MODE:SW -> ACC, probed only once."""
    responses = {
        k: v
        for k, v in AmonicsEDFA.sim_responses().items()
        if not (hasattr(k, "pattern") and "MODE" in k.pattern)
    }
    inst = AmonicsEDFA(SimTransport(responses, default=""), "edfa-test")
    inst.connect()
    assert inst.mode() == "ACC"
    sent_after_first = len(inst.transport.sent)
    assert inst.mode() == "ACC"  # cached: no further traffic
    assert len(inst.transport.sent) == sent_after_first


def test_set_mode_updates_cached_mode(edfa):
    assert edfa.mode() == "ACC"
    edfa.set_mode("APC")
    assert edfa.mode() == "APC"
    assert ":MODE:SW:CH1 APC" in edfa.transport.sent


def test_unit_conversion_amps_to_mA():
    """A unit reporting in A must be converted to mA."""
    responses = AmonicsEDFA.sim_responses()
    import re

    responses[re.compile(r":READ:DRIV:UNIT:\w+:CH\d\?")] = "A"
    responses[re.compile(r":DRIV:(ACC|APC):CUR:CH(\d)\?")] = "1.5"
    responses[re.compile(r":MODE:SW:CH\d\?")] = "ACC"
    inst = AmonicsEDFA(SimTransport(responses), "edfa-test")
    inst.connect()
    assert inst.setpoint() == pytest.approx(1500.0)
