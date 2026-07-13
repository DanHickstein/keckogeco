"""Tests for the TDS 2024C scope driver (tier 3, diagnostics only)."""

import numpy as np
import pytest

from keckogeco.config import DeviceConfig
from keckogeco.drivers.errors import ResponseError
from keckogeco.drivers.tds2024c import TDS2024C


@pytest.fixture
def scope():
    cfg = DeviceConfig(
        key="scope", driver="tds2024c", address="USB0::0x0699::0x03A6::C031910::INSTR"
    )
    inst = TDS2024C.from_config(cfg, sim=True)
    inst.connect()
    return inst


def test_get_trace_scaling(scope):
    time, voltage = scope.get_trace(channel=2)
    assert len(time) == len(voltage) == 2500
    # sim preamble: 1 us/point centered record -> +/- 1.25 ms
    assert time[0] == pytest.approx(-1.25e-3)
    assert time[-1] == pytest.approx(1.249e-3, rel=1e-3)
    # peak of 100 counts at 25 mV/count = 2.5 V, centered
    assert voltage.max() == pytest.approx(2.5)
    assert abs(time[np.argmax(voltage)]) < 5e-6
    # the transfer was configured for the full ASCII record
    assert "DAT:SOU CH2" in scope.transport.sent
    assert "DAT:ENC ASCI" in scope.transport.sent
    assert "DAT:STOP 2500" in scope.transport.sent


def test_bad_curve_raises(scope):
    scope.transport.responses["CURV?"] = "1,2,garbage"
    with pytest.raises(ResponseError, match="CURV"):
        scope.get_trace(channel=1)


def test_point_count_mismatch_raises(scope):
    scope.transport.responses["CURV?"] = "1,2,3"
    with pytest.raises(ResponseError, match="expected 2500 points"):
        scope.get_trace(channel=1)


def test_settings_and_status(scope):
    scope.time_scale_s = 5e-4
    assert scope.time_scale_s == pytest.approx(5e-4)
    scope.set_display(3, True)
    assert scope.display(3) is True
    scope.set_display(3, False)
    assert scope.display(3) is False
    assert scope.status() == {"time_scale_s": 5e-4, "displayed": [2]}


def test_command_formatting(scope):
    scope.set_vertical_scale_V(1, 0.5)
    scope.set_vertical_position_div(1, -2)
    scope.set_trigger_source(4)
    scope.set_trigger_level_V(0.1)
    for cmd in ("CH1:SCA 0.5", "CH1:POS -2", "TRIG:MAI:EDGE:SOU CH4", "TRIG:MAI:LEV 0.1"):
        assert cmd in scope.transport.sent


def test_channel_validation(scope):
    with pytest.raises(ValueError, match="channel"):
        scope.get_trace(channel=5)
    with pytest.raises(ValueError, match="channel"):
        scope.vertical_scale_V(0)
