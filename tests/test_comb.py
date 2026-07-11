"""Tests for the keyword registry, state machine, and controller (sim)."""

import pathlib

import pytest

from keckogeco.comb.keywords import KeywordError, KeywordRegistry, KeywordSpec, load_schema
from keckogeco.comb.state import CombState, SubsystemStatus, evaluate, legacy_code
from keckogeco.config import load_config

EXAMPLE = pathlib.Path(__file__).parent.parent / "config" / "instruments.example.toml"


# ------------------------------------------------------------------ schema


def test_schema_loads_77_keywords():
    schema = load_schema()
    assert len(schema) == 77
    assert schema["LFC_EDFA27_P"].writable
    assert schema["LFC_EDFA27_P"].units == "mW"
    assert schema["LFC_EDFA27_P"].max == 630
    assert schema["LFC_PTAMP_LATCH"].type == "enumerated"
    assert schema["LFC_PTAMP_LATCH"].enum["1"] == "ready"


def test_spec_conversions():
    spec = KeywordSpec(name="X", type="boolean", writable=True)
    assert spec.convert("on") is True
    assert spec.convert("0") is False
    with pytest.raises(KeywordError):
        spec.convert("maybe")
    arr = KeywordSpec(name="A", type="double array")
    assert arr.convert("1.0 2.5 3") == [1.0, 2.5, 3.0]
    enum = KeywordSpec(name="E", type="enumerated", enum={"1": "ready", "0": "stop"})
    assert enum.convert("ready") == 1
    assert enum.convert("0") == 0


def test_spec_validation_limits():
    spec = KeywordSpec(name="P", type="double", writable=True, min=0, max=630, units="mW")
    spec.validate(100.0)
    with pytest.raises(KeywordError, match="above maximum"):
        spec.validate(700.0)


# ---------------------------------------------------------------- registry


def test_registry_read_write_and_cache():
    reg = KeywordRegistry()
    store = {"p": 100.0}
    reg.bind("LFC_EDFA27_P", getter=lambda: store["p"], setter=lambda v: store.update(p=v))
    assert reg.read("LFC_EDFA27_P").value == 100.0
    reg.write("LFC_EDFA27_P", "250")
    assert store["p"] == 250.0
    assert reg.snapshot()["LFC_EDFA27_P"].value == 250.0


def test_registry_write_validates_schema_limits():
    reg = KeywordRegistry()
    reg.bind("LFC_EDFA27_P", getter=lambda: 0, setter=lambda v: None)
    with pytest.raises(KeywordError, match="above maximum"):
        reg.write("LFC_EDFA27_P", 631)


def test_registry_rejects_unknown_and_readonly():
    reg = KeywordRegistry()
    with pytest.raises(KeywordError, match="Unknown keyword"):
        reg.bind("NOT_A_KEYWORD", getter=lambda: 0)
    with pytest.raises(KeywordError, match="read-only"):
        reg.bind("LFC_PTAMP_OUT", setter=lambda v: None)


def test_registry_unbound_reports():
    reg = KeywordRegistry()
    assert "LFC_EDFA27_P" in reg.missing_getters()
    reg.bind("LFC_EDFA27_P", getter=lambda: 0)
    assert "LFC_EDFA27_P" not in reg.missing_getters()


# ------------------------------------------------------------------- state


def test_state_truth_table():
    all_on = SubsystemStatus(True, True, True, True, True, True)
    assert evaluate(all_on) == CombState.FULL_COMB
    assert legacy_code(all_on) == 30030
    standby = SubsystemStatus(False, True, True, True, True, True)
    assert evaluate(standby) == CombState.STANDBY
    assert legacy_code(standby) == 15015
    off = SubsystemStatus(False, False, False, False, False, False)
    assert evaluate(off) == CombState.OFF
    assert legacy_code(off) == 1
    strange = SubsystemStatus(True, False, True, True, True, True)
    assert evaluate(strange) == CombState.FAULT
    unknown = SubsystemStatus(None, True, True, True, True, True)
    assert evaluate(unknown) == CombState.UNKNOWN


# -------------------------------------------------------------- controller


@pytest.fixture
def controller():
    from keckogeco.comb.controller import LFCController

    config = load_config(EXAMPLE)
    ctrl = LFCController(config, sim=True)
    ctrl.start()
    yield ctrl
    ctrl.stop()


def test_controller_starts_all_sim_devices(controller):
    assert len(controller.devices) == len(controller.config.enabled_devices())
    assert not controller.offline


def test_controller_keyword_roundtrip(controller):
    controller.write("LFC_EDFA27_P", "150")
    assert controller.read("LFC_EDFA27_P").value == pytest.approx(150.0)


def test_controller_write_over_limit_rejected(controller):
    with pytest.raises(KeywordError, match="above maximum"):
        controller.write("LFC_EDFA27_P", 700)


def test_controller_edfa_onoff_sequence(controller):
    controller.write("LFC_EDFA27_ONOFF", "on")
    assert controller.read("LFC_EDFA27_ONOFF").value is True
    controller.write("LFC_EDFA27_ONOFF", "off")
    assert controller.read("LFC_EDFA27_ONOFF").value is False


def test_controller_state_summary_sim(controller):
    summary = controller.state_summary()
    assert summary["state"] == CombState.OFF.value  # everything off in sim
    assert summary["legacy_code"] == 1
    assert summary["sim"] is True
    controller.write("LFC_PTAMP_ONOFF", "1")
    assert controller.state_summary()["subsystems"]["ptamp"] is True


def test_controller_latch_and_shutter(controller):
    assert controller.read("LFC_PTAMP_LATCH").value == 1  # sim relay OK
    assert controller.read("LFC_YJ_SHUTTER").value is True
    controller.write("LFC_YJ_SHUTTER", "0")
    assert controller.read("LFC_YJ_SHUTTER").value is False


def test_controller_temperatures(controller):
    assert controller.read("LFC_T_GLY_RACK_IN").value == pytest.approx(25.5)
    assert controller.read("LFC_PPLN_T").value == pytest.approx(25.0)


def test_bound_keyword_count(controller):
    # tier-1 target: a solid fraction of the 77 keywords already answer
    assert len(controller.registry.bound) >= 30
    for name in controller.registry.bound:
        assert name in controller.registry.schema
