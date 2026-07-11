"""Tests for the USB-2408 thermocouple DAQ driver."""

import pytest

from keckogeco.config import DeviceConfig
from keckogeco.drivers.usb2408 import DEFAULT_POSITIONS, NUM_CHANNELS, USB2408


def test_sim_temperatures_and_positions():
    cfg = DeviceConfig(key="daq", driver="usb2408", address="0")
    daq = USB2408.from_config(cfg, sim=True)
    with daq:
        assert daq.temperature_C(0) == pytest.approx(23.0)
        temps = daq.all_temperatures_C()
        assert len(temps) == NUM_CHANNELS
        assert "Waveshaper (upper rack)" in temps
        assert daq.positions == DEFAULT_POSITIONS[0]


def test_positions_override():
    cfg = DeviceConfig(
        key="daq",
        driver="usb2408",
        address="1",
        options={"positions": [f"ch{i}" for i in range(NUM_CHANNELS)]},
    )
    daq = USB2408.from_config(cfg, sim=True)
    with daq:
        assert daq.positions[3] == "ch3"


def test_channel_out_of_range():
    cfg = DeviceConfig(key="daq", driver="usb2408", address="0")
    daq = USB2408.from_config(cfg, sim=True)
    with daq, pytest.raises(ValueError, match="channel"):
        daq.temperature_C(9)
