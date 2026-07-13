"""Tests for the action executor and the ported transition sequences (sim)."""

import pathlib
import time

import pytest

from keckogeco.comb.actions import ActionBusy
from keckogeco.comb.controller import LFCController
from keckogeco.comb.state import CombState
from keckogeco.config import load_config

EXAMPLE = pathlib.Path(__file__).parent.parent / "config" / "instruments.example.toml"


@pytest.fixture
def controller():
    config = load_config(EXAMPLE)
    ctrl = LFCController(config, sim=True)
    ctrl.start()
    yield ctrl
    ctrl.stop()


def wait_done(controller, timeout=10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = controller.executor.current()
        if current and not current["running"]:
            return current
        time.sleep(0.02)
    raise TimeoutError("action did not finish")


def test_minicomb_setup_reaches_standby(controller):
    assert controller.comb_state() == CombState.OFF
    controller.executor.submit("minicomb_auto_setup")
    result = wait_done(controller)
    assert result["error"] is None
    assert controller.comb_state() == CombState.STANDBY
    # commissioning values landed on the devices
    assert controller.device("rf_amp_psu").voltage_setpoint_V(1) == pytest.approx(30.0)
    assert controller.device("rf_osc_psu").voltage_setpoint_V(2) == pytest.approx(15.0)
    assert controller.device("edfa23").setpoint() == pytest.approx(0.0)  # parked: out of service
    assert controller.device("edfa23").activation is True  # but activated for the state code


def test_full_comb_from_off_and_back(controller):
    controller.executor.submit("set_full_comb")
    result = wait_done(controller)
    assert result["error"] is None
    assert controller.comb_state() == CombState.FULL_COMB
    assert controller.device("ptamp").pwramp_mA == pytest.approx(3900.0)
    assert controller.device("ptamp").preamp_mA == pytest.approx(600.0)

    controller.executor.submit("set_standby")
    result = wait_done(controller)
    assert result["error"] is None
    assert controller.comb_state() == CombState.STANDBY
    assert controller.device("ptamp").pwramp_mA == pytest.approx(0.0)

    controller.executor.submit("set_off")
    result = wait_done(controller)
    assert result["error"] is None
    assert controller.comb_state() == CombState.OFF


def test_single_slot_execution(controller):
    from keckogeco.comb import actions as actions_mod

    def slow_action(_controller, _ctx):
        time.sleep(0.5)  # real sleep: sim skips ctx.sleep, not this

    actions_mod.ACTIONS["_test_slow"] = slow_action
    try:
        controller.executor.submit("_test_slow")
        with pytest.raises(ActionBusy):
            controller.executor.submit("set_off")
        wait_done(controller)
    finally:
        del actions_mod.ACTIONS["_test_slow"]


def test_unknown_action_rejected(controller):
    with pytest.raises(KeyError, match="unknown action"):
        controller.executor.submit("make_coffee")


def test_transition_keyword_write_submits_action(controller):
    controller.write("LFC_SET_STANDBY", "1")
    result = wait_done(controller)
    assert result["name"] == "set_standby"
    assert controller.comb_state() == CombState.STANDBY


def test_ice_and_test_keywords(controller):
    assert controller.read("ICECLK").value > 0
    assert controller.read("ICESTA").value == 1
    controller.write("ICESTA", "3")
    assert controller.read("ICESTA").value == 3
    controller.write("TESTINT", "42")
    assert controller.read("TESTINT").value == 42
    controller.write("TESTARRAY", "1.5 2.5")
    assert controller.read("TESTARRAY").value == [1.5, 2.5]


def test_heartbeat_pokes_iceclk(controller):
    time.sleep(1.5)  # heartbeat period is 1 s
    snapshot = controller.registry.snapshot()
    assert "ICECLK" in snapshot
    controller.write("ICECLK_ONOFF", "0")
    assert controller.heartbeat.enabled is False


def test_wsp_keywords_program_waveshaper(controller):
    controller.write("LFC_WSP_PHASE", "-5.7")
    assert controller.read("LFC_WSP_PHASE").value == pytest.approx(-5.7)
    profiles = controller.device("waveshaper1").transport.loaded_profiles
    assert len(profiles) == 1
    controller.write("LFC_WSP_ATTEN", "3.0")
    assert len(profiles) == 2
    assert controller.device("waveshaper1").atten(193.0) == pytest.approx(3.0)


def test_tec_ramp_steps(controller):
    controller.write("LFC_PPLN_T", 27.0)  # from 25.0 -> steps of 0.5
    tec = controller.device("tec_ppln")
    assert tec.setpoint_C == pytest.approx(27.0)


def test_bound_coverage_now_much_higher(controller):
    # Phase 2: 50 of the 77 keywords answer; the rest need tier-2 drivers
    # (VOAs, Clarity, 2x2 switch, hk shutter) or the Phase-2b autolocks.
    assert len(controller.registry.bound) >= 50
