"""Tests for the tier-2 drivers: pendulum, OSA, VOA, HK shutter."""

import numpy as np
import pytest

from keckogeco.config import DeviceConfig


def make(driver_module, cls_name, key, address, **options):
    import importlib

    module = importlib.import_module(f"keckogeco.drivers.{driver_module}")
    cfg = DeviceConfig(key=key, driver=driver_module, address=address, options=options)
    inst = getattr(module, cls_name).from_config(cfg, sim=True)
    inst.connect()
    return inst


def test_pendulum_measures_16ghz():
    counter = make("pendulum_cnt90", "PendulumCNT90", "pendulum", "GPIB0::10::INSTR")
    assert counter.measure_frequency_Hz("c") == pytest.approx(16e9)
    assert ":CONF:FREQ (@3)" in counter.transport.sent
    with pytest.raises(ValueError, match="channel"):
        counter.measure_frequency_Hz("x")
    with pytest.raises(ValueError, match="meas_time"):
        counter.measure_frequency_Hz("c", meas_time_s=2000)


def test_osa_spectrum():
    osa = make("agilent_86142b", "Agilent86142B", "osa", "GPIB0::30::INSTR")
    wl, power = osa.get_spectrum("A")
    assert len(wl) == len(power) == 501
    assert wl[0] == pytest.approx(1545.0)
    assert wl[-1] == pytest.approx(1575.0)
    assert power.max() > power.min()
    # envelope peaks near 1560 nm in the sim spectrum
    assert abs(wl[np.argmax(power)] - 1560) < 2


def test_osa_wavelength_settings():
    osa = make("agilent_86142b", "Agilent86142B", "osa", "GPIB0::30::INSTR")
    osa.wl_start_nm = 1550.0
    osa.wl_stop_nm = 1570.0
    assert osa.wl_start_nm == pytest.approx(1550.0)
    assert osa.wl_center_nm == pytest.approx(1560.0)
    assert osa.wl_span_nm == pytest.approx(20.0)


def test_osa_set_range_never_inverts():
    osa = make("agilent_86142b", "Agilent86142B", "osa", "GPIB0::30::INSTR")
    # sim starts at 1545-1575; a range entirely above the current stop must
    # write the new stop first so start <= stop holds throughout
    osa.set_range(1600.0, 1650.0)
    assert osa.wl_start_nm == pytest.approx(1600.0)
    assert osa.wl_stop_nm == pytest.approx(1650.0)
    sent = [c for c in osa.transport.sent if c.startswith("SENS:WAV")]
    assert sent.index("SENS:WAV:STOP 1650.00nm") < sent.index("SENS:WAV:STAR 1600.00nm")
    osa.set_range(stop_nm=1660.0)  # partial update
    assert osa.wl_start_nm == pytest.approx(1600.0)
    assert osa.wl_stop_nm == pytest.approx(1660.0)


def test_osa_resolution_sensitivity_sweep():
    osa = make("agilent_86142b", "Agilent86142B", "osa", "GPIB0::30::INSTR")
    assert osa.resolution_nm == pytest.approx(0.06)  # sim default = best
    assert osa.RESOLUTIONS_NM[0] == 0.06
    osa.resolution_nm = 0.5
    assert osa.resolution_nm == pytest.approx(0.5)
    osa.sensitivity_dBm = -75.0
    assert osa.sensitivity_dBm == pytest.approx(-75.0)
    assert osa.sweep_continuous is True
    osa.sweep_continuous = False
    assert osa.sweep_continuous is False
    osa.trigger_single()
    assert "INIT:IMM" in osa.transport.sent
    status = osa.status()
    assert status["resolution_nm"] == pytest.approx(0.5)
    assert status["sweep_continuous"] is False


def test_voa_attenuation_roundtrip():
    voa = make("oz_voa", "OZOpticsVOA", "voa1550", "ASRL7::INSTR")
    assert voa.attenuation_dB == pytest.approx(0.0)
    voa.attenuation_dB = 12.5
    assert voa.attenuation_dB == pytest.approx(12.5)
    assert "A12.50" in voa.transport.sent


def test_voa_unknown_attenuation_is_nan():
    """Fresh power-up: unit answers 'Atten:unknown' until first move."""
    import math

    voa = make("oz_voa", "OZOpticsVOA", "voa1550", "ASRL7::INSTR")
    voa.transport.responses["A?"] = "Atten:unknown"
    assert math.isnan(voa.attenuation_dB)


def test_voa_not_homed_state_pins_until_set():
    """Once 'unknown' is seen, reads skip the hardware (the rack VOAs are
    unused; don't spend poll time on them); a set re-enables real reads."""
    import math

    voa = make("oz_voa", "OZOpticsVOA", "voa1550", "ASRL7::INSTR")
    real_query = voa.transport.responses["A?"]
    voa.transport.responses["A?"] = "Atten:unknown"
    assert math.isnan(voa.attenuation_dB)
    sent_after_first = len(voa.transport.sent)
    assert math.isnan(voa.attenuation_dB)  # pinned: no further traffic
    assert len(voa.transport.sent) == sent_after_first
    voa.transport.responses["A?"] = real_query
    voa.attenuation_dB = 3.5
    assert voa.attenuation_dB == pytest.approx(3.5)


def test_amonics_wakeup_retries_on_same_connection():
    """PM-13/PM-23 drop the first command after port-open; the wake-up
    handshake must resend on the same open transport."""
    from keckogeco.drivers.amonics_edfa import AmonicsEDFA
    from tests.test_instrument_base import FlakyTransport

    transport = FlakyTransport(fail_count=2, responses=AmonicsEDFA.sim_responses())
    edfa = AmonicsEDFA(transport, "edfa13")
    edfa.connect()  # would raise without the retry loop
    assert transport.open_count == 1  # retried WITHOUT reopening the port
    assert edfa.output_power_mW() == pytest.approx(150.0)


def test_hk_shutter_toggle_semantics():
    shutter = make("hk_shutter", "HKShutter", "hk_shutter", "COM12")
    assert shutter.open is False
    shutter.set_open(True)
    assert shutter.open is True
    sent_before = len(shutter.transport.sent)
    shutter.set_open(True)  # already open: must NOT toggle again
    toggles = [c for c in shutter.transport.sent[sent_before:] if c == "ens"]
    assert not toggles
    shutter.set_open(False)
    assert shutter.open is False


def test_agiltron_switch_positions():
    switch = make("agiltron_switch", "AgiltronSwitch2x2", "switch2x2", "COM12")
    assert switch.position == 1  # sim powers up on the YJ path
    switch.set_position(2)
    assert switch.position == 2
    assert switch.status() == {"position": 2, "route": "HK"}
    sent_before = len(switch.transport.sent)
    switch.set_position(2)  # already there: must not send the 0x14 set frame
    assert not any(cmd.startswith("0114") for cmd in switch.transport.sent[sent_before:])
    with pytest.raises(ValueError, match="position"):
        switch.set_position(3)


def test_clarity_status_and_output():
    laser = make("clarity", "Clarity", "clarity", "ASRL23::INSTR")
    assert laser.status_code == 0
    assert laser.output is False
    laser.set_output(True)
    assert laser.output is True
    assert laser.status() == {"status": "locked", "output": True}
    assert "SOUR:STAT 1" in laser.transport.sent


def test_keysight_fg_channel_roundtrip():
    fg = make("keysight_fg33500", "KeysightFG33500", "fg1", "USB0::0x0957::0x2807::MY1::INSTR")
    assert "33512B" in fg.idn()
    fg.set_frequency_Hz(2, 5e6)
    fg.set_amplitude_V(2, 0.25)
    fg.set_offset_V(2, 0.1)
    fg.set_function(2, "square")
    fg.set_output(2, True)
    params = fg.channel_parameters(2)
    assert params["frequency_Hz"] == pytest.approx(5e6)
    assert params["amplitude_V"] == pytest.approx(0.25)
    assert params["offset_V"] == pytest.approx(0.1)
    assert params["function"] == "SQU"
    assert params["output"] is True
    # channel 1 untouched
    assert fg.frequency_Hz(1) == pytest.approx(1000.0)
    assert fg.output(1) is False
    with pytest.raises(ValueError, match="channel"):
        fg.frequency_Hz(3)
    with pytest.raises(ValueError, match="function"):
        fg.set_function(1, "noise")


def test_im_auto_lock_sim():
    """The lock finds a midpoint bias on the sim's sinusoidal response."""
    from keckogeco.comb.locking import im_auto_lock
    from keckogeco.drivers.srs_sim900 import SIM900

    cfg = DeviceConfig(key="srs", driver="srs_sim900", address="ASRL21::INSTR")
    srs = SIM900.from_config(cfg, sim=True)
    srs.connect()
    servo = srs.sim960(5)
    result = im_auto_lock(servo, sim=True)
    assert -2.0 <= result["bias_V"] <= 1.0
    assert servo.output_mode == "PID"
    assert servo.setpoint_V == pytest.approx(result["setpoint_V"], abs=1e-3)
    # sim response 0.5*sin(2v+1): max 0.5, min -0.5 within the sweep
    assert result["sweep_max_V"] == pytest.approx(0.5, abs=0.01)
    assert result["sweep_min_V"] == pytest.approx(-0.5, abs=0.01)
