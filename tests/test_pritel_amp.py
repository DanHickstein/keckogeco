"""Tests for the Pritel amplifier driver against SimTransport."""

import pytest

from keckogeco.config import DeviceConfig
from keckogeco.drivers.errors import ConnectionLost
from keckogeco.drivers.pritel_amp import PritelAmp, to_mA
from keckogeco.drivers.transports import SimTransport


class FlakyTransport(SimTransport):
    """SimTransport whose next ``fail_next`` queries time out silently."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fail_next = 0
        self.close_calls = 0

    def query(self, cmd):
        if self.fail_next > 0:
            self.fail_next -= 1
            self.sent.append(cmd)
            raise TimeoutError("VI_ERROR_TMO (simulated)")
        return super().query(cmd)

    def close(self):
        self.close_calls += 1
        super().close()


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


def test_pump_off_aborts_running_ramp(amp):
    """set_pump(False) mid-ramp (from another thread in real life) stops
    the stepping and parks the power amp at 0 — the operator could not
    turn the Pritel off during the upward ramp (2026-07-18)."""
    amp.set_pump(True)
    # simulate "pump-off arrives after the 3rd step" via the abort hook's
    # sibling: set_pump(False) sets _ramp_abort; here we trigger it from
    # the sim transport so the timing is deterministic
    original = amp.transport.write
    steps = {"n": 0}

    def write_counting(cmd):
        original(cmd)
        if cmd.startswith("FA SETPWR"):
            steps["n"] += 1
            if steps["n"] == 3:
                amp._ramp_abort.set()  # what a concurrent set_pump(False) does

    amp.transport.write = write_counting
    amp.set_pwramp_mA(3900)
    setpwr = [c for c in amp.transport.sent if c.startswith("FA SETPWR")]
    # 3 steps went out, then the abort parked the stage at 0
    assert len(setpwr) == 4
    assert setpwr[-1] == "FA SETPWR 000"
    assert amp.pwramp_mA == pytest.approx(0.0)


def test_action_abort_check_stops_ramp(amp):
    """The executor's abort (polled via abort_check) stops a ramp before
    the next step; the stage parks at 0 and the call returns."""
    aborted = {"now": False}
    calls = {"n": 0}

    def abort_check():
        calls["n"] += 1
        aborted["now"] = calls["n"] > 2
        return aborted["now"]

    amp.set_preamp_mA(600, abort_check=abort_check)
    setpre = [c for c in amp.transport.sent if c.startswith("FA SETPRE")]
    assert setpre[-1] == "FA SETPRE 000"
    assert len(setpre) == 3  # two real steps, then the park-at-0
    assert amp.preamp_mA == pytest.approx(0.0)


def test_pump_off_sets_abort_and_next_ramp_clears_it(amp):
    amp.set_pump(False)
    assert amp._ramp_abort.is_set()
    amp.set_pump(True)  # pump ON must not request an abort
    amp.set_preamp_mA(100)  # a fresh ramp clears the stale flag and runs
    setpre = [c for c in amp.transport.sent if c.startswith("FA SETPRE")]
    assert setpre[-1] == "FA SETPRE 100"
    assert not amp._ramp_abort.is_set()
    assert amp.preamp_mA == pytest.approx(100.0)


def test_status_dict(amp):
    status = amp.status()
    assert status["pump_on"] is False
    assert set(status) >= {"preamp_mA", "pwramp_mA", "input_power_mW", "output_power_mW"}


@pytest.fixture
def flaky_amp():
    transport = FlakyTransport(PritelAmp.sim_responses(), address="SIM::flaky")
    inst = PritelAmp(transport, "ptamp")
    inst.connect()
    transport.sent.clear()
    return inst


def test_dropped_command_resent_on_open_port(flaky_amp):
    """One silent drop recovers by re-sending, without close/reopen."""
    flaky_amp.transport.fail_next = 1
    assert flaky_amp.input_power_mW == pytest.approx(1.0)
    assert flaky_amp.transport.sent == ["FA INPUT?", "FA INPUT?"]
    assert flaky_amp.transport.close_calls == 0


def test_two_drops_escalate_to_reconnect(flaky_amp):
    """Resend failing too falls back to the base reconnect-once path."""
    flaky_amp.transport.fail_next = 2
    assert flaky_amp.input_power_mW == pytest.approx(1.0)
    assert flaky_amp.transport.close_calls == 1  # reconnect happened


def test_persistent_silence_raises_connection_lost(flaky_amp):
    flaky_amp.transport.fail_next = 99
    with pytest.raises(ConnectionLost):
        _ = flaky_amp.input_power_mW
