"""Tests for the keyword registry, state machine, and controller (sim)."""

import pathlib

import pytest

from keckogeco.comb.keywords import KeywordError, KeywordRegistry, KeywordSpec, load_schema
from keckogeco.comb.state import CombState, SubsystemStatus, evaluate, legacy_code
from keckogeco.config import load_config

EXAMPLE = pathlib.Path(__file__).parent.parent / "config" / "instruments.example.toml"


# ------------------------------------------------------------------ schema


def test_schema_loads_89_keywords():
    # 77 baseline keywords + additions listed in ktl/keyword-changes.md
    # (LFC_WSP_TOD, LFC_WSP_CENTER, EDFA output monitors x3, EDFA13 input
    # monitor, LFC_PTAMP_IN, LFC_PTAMP_INTERLOCK_V, LFC_REPRATE, and the
    # reference chain: LFC_REPRATE_REF, LFC_RBCLOCK_PHASELOCK/FREQLOCK)
    schema = load_schema()
    assert len(schema) == 89
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


def test_controller_switch_and_clarity(controller):
    assert controller.read("LFC_2BY2_SWITCH").value == 1  # sim starts on YJ
    controller.write("LFC_2BY2_SWITCH", "2")
    assert controller.read("LFC_2BY2_SWITCH").value == 2
    assert controller.read("LFC_CLARITY_ONOFF").value == 0
    controller.write("LFC_CLARITY_ONOFF", "1")
    assert controller.read("LFC_CLARITY_ONOFF").value == 1


def test_startup_is_passive(controller):
    """Connecting and binding must never enable emission or push setpoints.

    The Pritel power amp (3.9 A) is the most dangerous switch in the
    system: it may only come up through an explicit action or keyword
    write, never as a side effect of starting the server.
    """
    assert controller.device("ptamp").pump_on is False
    assert controller.device("ptamp").pwramp_mA == pytest.approx(0.0)
    assert controller.device("edfa27").activation is False
    assert controller.device("edfa23").activation is False
    for key in ("rf_osc_psu", "rf_amp_psu"):
        assert controller.device(key).output_on(controller.psu_channel(key)) is False


def test_controller_presets_and_monitors(controller):
    # write-1 presets push the commissioned setpoints
    controller.write("LFC_RFOSCI_DEFAULT", "1")
    psu = controller.device("rf_osc_psu")
    ch = controller.psu_channel("rf_osc_psu")
    assert psu.voltage_setpoint_V(ch) == pytest.approx(15.0)
    assert psu.current_setpoint_A(ch) == pytest.approx(3.0)
    assert controller.read("LFC_RFOSCI_DEFAULT").value is False  # reads stay False
    controller.write("LFC_EDFA27_AUTO_ON", "1")
    assert controller.read("LFC_EDFA27_ONOFF").value is True
    # monitors report OK while outputs are off / temps nominal in sim
    assert controller.read("LFC_RFOSCI_MONITOR").value is True
    assert controller.read("LFC_RFAMP_MONITOR").value is True
    assert controller.read("LFC_TEMP_MONITOR").value is True
    temps = controller.read("LFC_TEMP_TEST1").value
    assert len(temps) == 8


def test_controller_rep_rate_keyword(controller):
    # RF chain off: NaN (nothing to count; the GUI shows an em dash) —
    # never an error, so the snapshot poll stays cheap with the RF down
    import math

    assert math.isnan(controller.read("LFC_REPRATE").value)
    controller.write("LFC_RFOSCI_ONOFF", "1")
    controller.write("LFC_RFAMP_ONOFF", "1")
    assert controller.read("LFC_REPRATE").value == pytest.approx(16e9)
    # full-resolution gate (12 digits/s on the CNT-90XL), and one gated
    # measurement serves every caller inside the cache window — the
    # /state poll and the keyword poller must not re-gate per call
    sent = controller.device("pendulum").transport.sent
    assert ":ACQ:APER 1.0" in sent
    inits = sent.count(":INIT")
    controller.read("LFC_REPRATE")
    controller.state_summary()
    assert controller.device("pendulum").transport.sent.count(":INIT") == inits


def test_controller_im_rf_att_and_lock_mode(controller):
    controller.write("LFC_IM_RF_ATT", "0.72")
    assert controller.read("LFC_IM_RF_ATT").value == pytest.approx(0.72)
    controller.write("LFC_IM_LOCK_MODE", "1")
    assert controller.read("LFC_IM_LOCK_MODE").value is True
    controller.write("LFC_IM_LOCK_MODE", "0")
    assert controller.read("LFC_IM_LOCK_MODE").value is False


def test_all_keywords_bound_with_full_config(controller):
    # with every example-config device present, only the keywords whose
    # device is absent from the example config may remain unbound
    allowed_unbound = {
        "LFC_TEMP_TEST2",  # daq_eocb (EO comb board DAQ) not in example config
        "LFC_T_EOCB_IN",
        "LFC_T_EOCB_OUT",
        # IM bias locking is deliberately manual; the auto-lock keyword is
        # unbound and proposed for retirement (ktl/keyword-changes.md)
        "LFC_IM_AUTO_LOCK",
        # VOA keys stay unit-serial-based until each unit's wavelength is
        # identified on-site; the wavelength keywords bind only after a
        # config block is renamed to voa1310/voa1550/voa2000
        "LFC_VOA1310_ATTEN",
        "LFC_VOA1550_ATTEN",
        "LFC_VOA2000_ATTEN",
    }
    unbound = {n for n in controller.registry.schema if n not in controller.registry.bound}
    assert unbound <= allowed_unbound


def test_reference_chain_keywords(controller):
    # FS725 lock health and the counter's timebase source (EXT = the
    # shared Rb 10 MHz; INT means LFC_REPRATE silently loses its
    # Rb discipline even though the value itself still reads)
    assert controller.read("LFC_RBCLOCK_PHASELOCK").value is True
    assert controller.read("LFC_RBCLOCK_FREQLOCK").value is True
    assert controller.read("LFC_REPRATE_REF").value == "EXT"


def test_bound_keyword_count(controller):
    # tier-1 target: a solid fraction of the 77 keywords already answer
    assert len(controller.registry.bound) >= 30
    for name in controller.registry.bound:
        assert name in controller.registry.schema
