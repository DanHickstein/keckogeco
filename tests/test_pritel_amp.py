"""Tests for the Pritel amplifier driver against SimTransport."""

import pytest

from keckogeco.config import DeviceConfig
from keckogeco.drivers.pritel_amp import PritelAmp, to_mA


@pytest.fixture
def amp():
    cfg = DeviceConfig(key="ptamp", driver="pritel_amp", address="ASRL6::INSTR")
    inst = PritelAmp.from_config(cfg, sim=True)
    inst.connect()
    inst.transport.sent.clear()  # drop the READY? handshake
    return inst


def test_to_mA_conversions():
    assert to_mA(250) == 250.0
    assert to_mA("250") == 250.0
    assert to_mA("250mA") == 250.0
    assert to_mA("0.5A") == 500.0
    assert to_mA("3.9A") == pytest.approx(3900.0)


def test_monitors(amp):
    assert amp.input_power_mW == pytest.approx(1.0)
    assert amp.output_power_mW == pytest.approx(0.0)
    assert "AutoShutDown" in amp.auto_shutdown_status


def test_pump_on_off(amp):
    assert amp.pump_on is False
    amp.set_pump(True)
    assert amp.pump_on is True
    amp.set_pump(False)
    assert amp.pump_on is False


def test_preamp_ramps_in_steps(amp):
    amp.set_preamp_mA(300)
    setpre = [c for c in amp.transport.sent if c.startswith("FA SETPRE")]
    # 0 -> 300 mA at 100 mA steps: intermediate values, ending at 300
    assert len(setpre) >= 3
    assert setpre[-1] == "FA SETPRE 300"
    assert amp.preamp_mA == pytest.approx(300.0)


def test_preamp_no_ramp(amp):
    amp.set_preamp_mA(200, ramp=False)
    setpre = [c for c in amp.transport.sent if c.startswith("FA SETPRE")]
    assert setpre == ["FA SETPRE 200"]


def test_preamp_over_limit_raises(amp):
    with pytest.raises(ValueError, match="exceeds max"):
        amp.set_preamp_mA(601)
    assert not [c for c in amp.transport.sent if c.startswith("FA SETPRE")]


def test_pwramp_command_encoding(amp):
    """5800 mA must be sent as 'FA SETPWR 580' (0.01 A units)."""
    amp.set_pump(True)
    amp.set_pwramp_mA(5800, ramp=False)
    setpwr = [c for c in amp.transport.sent if c.startswith("FA SETPWR")]
    assert setpwr == ["FA SETPWR 580"]
    assert amp.pwramp_mA == pytest.approx(5800.0)


def test_pwramp_rounds_to_10mA(amp):
    amp.set_pump(True)
    amp.set_pwramp_mA(1234, ramp=False)
    assert amp.pwramp_mA == pytest.approx(1230.0)


def test_pwramp_over_limit_raises(amp):
    with pytest.raises(ValueError, match="exceeds max"):
        amp.set_pwramp_mA(5900)


def test_pwramp_ramp_reaches_target(amp):
    amp.set_pump(True)
    amp.set_pwramp_mA(500)
    setpwr = [c for c in amp.transport.sent if c.startswith("FA SETPWR")]
    assert len(setpwr) >= 5  # 0 -> 500 at 50 mA steps
    assert amp.pwramp_mA == pytest.approx(500.0)


def test_status_dict(amp):
    status = amp.status()
    assert status["pump_on"] is False
    assert set(status) >= {"preamp_mA", "pwramp_mA", "input_power_mW", "output_power_mW"}
