"""Tests for the merged Instek PSU driver (GPD-4303S + GPP profiles)."""

import pytest

from keckogeco.config import DeviceConfig
from keckogeco.drivers.instek_psu import PROFILES, InstekPSU


def make(model="GPD-4303S", key="rf_osc_psu"):
    cfg = DeviceConfig(
        key=key, driver="instek_psu", address="ASRL5::INSTR", options={"model": model}
    )
    inst = InstekPSU.from_config(cfg, sim=True)
    inst.connect()
    return inst


def test_model_from_config():
    gpd = make("GPD-4303S")
    assert gpd.model == "GPD-4303S"
    assert gpd.profile.channels == 4
    gpp = make("GPP-1326", key="rf_amp_psu")
    assert gpp.profile.channels == 1
    assert not gpp.profile.master_output


def test_unknown_model_rejected():
    with pytest.raises(ValueError, match="unknown Instek model"):
        make("GPD-9999")


def test_transport_defaults_follow_model():
    cfg_gpd = DeviceConfig(
        key="a", driver="instek_psu", address="X", options={"model": "GPD-4303S"}
    )
    cfg_gpp = DeviceConfig(key="b", driver="instek_psu", address="X", options={"model": "GPP-1326"})
    assert InstekPSU.transport_defaults(cfg_gpd)["baud_rate"] == 9600
    assert InstekPSU.transport_defaults(cfg_gpp)["baud_rate"] == 115200
    assert InstekPSU.transport_defaults(cfg_gpd)["read_termination"] == "\r\n"


def test_setpoint_roundtrip():
    psu = make()
    psu.set_voltage_V(15.0, channel=2)
    psu.set_current_A(3.0, channel=2)
    assert psu.voltage_setpoint_V(2) == pytest.approx(15.0)
    assert psu.current_setpoint_A(2) == pytest.approx(3.0)
    assert psu.output_voltage_V(2) == pytest.approx(15.0)  # sim mirrors setpoint


def test_channel_limits_raise():
    psu = make()
    with pytest.raises(ValueError, match="exceeds"):
        psu.set_voltage_V(31, channel=1)
    with pytest.raises(ValueError, match="exceeds"):
        psu.set_current_A(3.5, channel=1)
    with pytest.raises(ValueError, match="exceeds"):
        psu.set_current_A(1.5, channel=4)  # CH4 max 1 A


def test_gpd_ch3_coupled_limit():
    psu = make()
    psu.set_current_A(2.0, channel=3)  # fine below 5 V
    with pytest.raises(ValueError, match="lower ISET"):
        psu.set_voltage_V(8.0, channel=3)  # >5 V while ISET >1 A


def test_channel_out_of_range():
    gpp = make("GPP-1326")
    with pytest.raises(ValueError, match="out of range"):
        gpp.set_voltage_V(1.0, channel=2)


def test_master_output_gpd():
    psu = make()
    assert psu.output_on() is False
    psu.set_output(True)
    assert psu.output_on() is True
    psu.set_output(False)
    assert psu.output_on() is False


def test_per_channel_output_gpp():
    gpp = make("GPP-1326")
    assert gpp.output_on(1) is False
    gpp.set_output(True, channel=1)
    assert gpp.output_on(1) is True


def test_profiles_complete():
    for name, profile in PROFILES.items():
        assert len(profile.limits) == profile.channels, name


def test_status_dict():
    psu = make()
    status = psu.status()
    assert status["model"] == "GPD-4303S"
    assert set(status["channels"]) == {1, 2, 3, 4}
