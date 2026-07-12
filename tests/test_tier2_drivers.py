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


def test_voa_attenuation_roundtrip():
    voa = make("oz_voa", "OZOpticsVOA", "voa1550", "ASRL7::INSTR")
    assert voa.attenuation_dB == pytest.approx(0.0)
    voa.attenuation_dB = 12.5
    assert voa.attenuation_dB == pytest.approx(12.5)
    assert "A12.50" in voa.transport.sent


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
