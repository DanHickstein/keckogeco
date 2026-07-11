"""Tests for the WaveShaper driver (sim link)."""

import numpy as np
import pytest

from keckogeco.config import DeviceConfig
from keckogeco.drivers.waveshaper import (
    MAX_ATTEN_DB,
    Waveshaper,
    dispersion_phase,
    nm_to_thz,
    thz_to_nm,
)


@pytest.fixture
def ws():
    cfg = DeviceConfig(key="waveshaper1", driver="waveshaper", address="SN201904")
    inst = Waveshaper.from_config(cfg, sim=True)
    inst.connect()
    return inst


def test_frequency_grid(ws):
    assert ws.start_THz == pytest.approx(191.25)
    assert ws.stop_THz == pytest.approx(196.275)
    assert len(ws.freq_THz) == pytest.approx(5025, abs=2)


def test_nm_thz_roundtrip():
    assert thz_to_nm(nm_to_thz(1560.0)) == pytest.approx(1560.0)
    assert nm_to_thz(1560.0) == pytest.approx(192.17, abs=0.01)


def test_bandpass(ws):
    ws.set_bandpass(center=192.175, span=4, unit="thz")
    assert ws.atten(192.0) == 0.0  # inside [190.175, 194.175]
    assert ws.atten(196.0) == MAX_ATTEN_DB
    assert ws.atten(190.0) == MAX_ATTEN_DB


def test_bandpass_nm_unit(ws):
    ws.set_bandpass(center=1560.0, span=10, unit="nm")
    center_thz = nm_to_thz(1560.0)
    assert ws.atten(center_thz) == 0.0
    assert ws.atten(center_thz + 2.0) == MAX_ATTEN_DB


def test_dispersion_phase_zero_at_center():
    center_f = nm_to_thz(1560.0)
    assert dispersion_phase(center_f, d2_ps_nm=-5.7) == pytest.approx(0.0)
    # symmetric and positive-curvature for d2>0... sign follows beta2
    phase = dispersion_phase(np.array([center_f - 1, center_f + 1]), d2_ps_nm=1.0)
    assert phase[0] == pytest.approx(phase[1])


def test_write_profile_uploads_grid(ws):
    ws.set_bandpass(center=192.175, span=4, unit="thz")
    ws.set_dispersion(d2_ps_nm=-5.7)
    ws.write_profile()
    profiles = ws.transport.loaded_profiles
    assert len(profiles) == 1
    lines = profiles[0].strip().split("\n")
    assert len(lines) == len(ws.freq_THz)
    freq, atten, phase, port = lines[0].split("\t")
    assert float(freq) == pytest.approx(191.25)
    assert port == "1"


def test_write_profile_rejects_bad_lengths(ws):
    with pytest.raises(ValueError, match="points"):
        ws.write_profile(amp=np.zeros(10))


def test_flatten_from_spectrum(ws):
    # synthetic comb: lines every 0.13 nm with a tilted envelope, kept
    # inside the device's 191.25-196.275 THz (~1527-1567.6 nm) range
    wl = np.linspace(1550, 1568, 4000)
    power = np.full_like(wl, -40.0)
    for line_wl in np.arange(1552, 1566, 0.13):
        idx = np.argmin(np.abs(wl - line_wl))
        power[idx] = -10 + (line_wl - 1560) * 0.5  # tilt
    peak_wl, peak_pw = ws.flatten_from_spectrum(wl, power, max_atten_dB=5)
    assert peak_wl.size > 50
    # strongest line gets max attenuation, weakest ~0
    strongest = nm_to_thz(peak_wl[np.argmax(peak_pw)])
    weakest = nm_to_thz(peak_wl[np.argmin(peak_pw)])
    assert ws.atten(strongest) == pytest.approx(5.0, abs=0.2)
    assert ws.atten(weakest) <= 1.0


def test_status(ws):
    ws.set_bandpass(center=192.175, span=4)
    status = ws.status()
    assert "bandpass" in status["atten"]
