"""Tests for the Yokogawa AQ63xx OSA driver (sim transport only)."""

import numpy as np
import pytest

from keckogeco.config import DeviceConfig
from keckogeco.drivers.yokogawa_osa import YokogawaOSA


def make(**options):
    cfg = DeviceConfig(
        key="osa_yokogawa", driver="yokogawa_osa", address="GPIB1::1::INSTR", options=options
    )
    osa = YokogawaOSA.from_config(cfg, sim=True)
    osa.connect()
    return osa


def test_connect_checks_identity():
    osa = make()
    assert osa.identity.startswith("YOKOGAWA,AQ6376")
    # legacy-mode escape is sent before anything else
    assert osa.transport.sent[0] == "CFORM1"


def test_spectrum():
    osa = make()
    wl, power = osa.get_spectrum("A")
    assert len(wl) == len(power) == 1001
    assert wl[0] == pytest.approx(1540.0)
    assert wl[-1] == pytest.approx(1580.0)
    # sim envelope peaks at the window center
    assert abs(wl[np.argmax(power)] - 1560) < 2
    with pytest.raises(ValueError, match="trace"):
        osa.get_spectrum("Z")


def test_wavelength_settings():
    osa = make()
    osa.wl_start_nm = 1500.0
    osa.wl_stop_nm = 1600.0
    assert osa.wl_start_nm == pytest.approx(1500.0)
    assert osa.wl_center_nm == pytest.approx(1550.0)
    assert osa.wl_span_nm == pytest.approx(100.0)
    # center/span go through the instrument's native commands
    osa.wl_center_nm = 2000.0
    assert osa.wl_start_nm == pytest.approx(1950.0)
    osa.wl_span_nm = 50.0
    assert osa.wl_start_nm == pytest.approx(1975.0)
    assert osa.wl_stop_nm == pytest.approx(2025.0)


def test_set_range_never_inverts():
    osa = make()
    # sim starts at 1540-1580; a range entirely above the current stop must
    # write the new stop first so start <= stop holds throughout
    osa.set_range(1600.0, 1650.0)
    assert osa.wl_start_nm == pytest.approx(1600.0)
    assert osa.wl_stop_nm == pytest.approx(1650.0)
    sent = [c for c in osa.transport.sent if c.startswith(":SENS:WAV")]
    assert sent.index(":SENS:WAV:STOP 1650.00NM") < sent.index(":SENS:WAV:STAR 1600.00NM")
    osa.set_range(stop_nm=1660.0)  # partial update
    assert osa.wl_stop_nm == pytest.approx(1660.0)


def test_resolution_and_reference_level():
    osa = make()
    assert osa.resolution_nm == pytest.approx(0.1)
    osa.resolution_nm = 0.5
    assert osa.resolution_nm == pytest.approx(0.5)
    osa.reference_level_dBm = -30.0
    assert osa.reference_level_dBm == pytest.approx(-30.0)
    assert ":DISP:TRAC:Y1:SCAL:RLEV -30.0DBM" in osa.transport.sent


def test_sensitivity_enumeration():
    osa = make()
    assert osa.sensitivity == "HIGH1"  # sim default index 3
    osa.sensitivity = "NHLD"
    assert osa.sensitivity == "NHLD"
    osa.sensitivity = "norm"  # alias, case-insensitive
    assert osa.sensitivity == "NORMAL"
    with pytest.raises(ValueError, match="sensitivity"):
        osa.sensitivity = "ULTRA"


def test_sweep_control():
    osa = make()
    assert osa.sweep_mode == "REPEAT"  # sim default
    osa.sweep("SINGLE")
    assert ":INIT:SMOD SING" in osa.transport.sent
    assert ":INIT" in osa.transport.sent
    assert osa.sweep_mode == "SINGLE"
    osa.sweep()  # restart without changing mode
    osa.abort()
    assert ":ABOR" in osa.transport.sent
    with pytest.raises(ValueError, match="sweep mode"):
        osa.sweep_mode = "WARP"


def test_status_keys():
    osa = make()
    status = osa.status()
    assert set(status) == {
        "wl_start_nm",
        "wl_stop_nm",
        "resolution_nm",
        "sensitivity",
        "sweep_mode",
        "reference_level_dBm",
    }
    assert status["sweep_mode"] == "REPEAT"
