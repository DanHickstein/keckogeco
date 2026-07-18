"""Tests for the Thorlabs ELL12 flattener ND-filter slider driver."""

import pytest

from keckogeco.config import DeviceConfig
from keckogeco.drivers.errors import ResponseError
from keckogeco.drivers.thorlabs_ell12 import ThorlabsELL12


def make(**options) -> ThorlabsELL12:
    cfg = DeviceConfig(key="nd_slider", driver="thorlabs_ell12", address="COM30", options=options)
    slider = ThorlabsELL12.from_config(cfg, sim=True)
    slider.connect()
    return slider


def test_connect_parses_info_and_slot_spacing():
    slider = make()
    assert slider.info["device_type"] == 0x0C  # ELL12
    assert slider.info["serial"] == "11223344"
    assert slider.info["travel_mm"] == 160
    assert slider.info["pulses_per_mm"] == 32
    # six slots span the travel: 160 mm * 32 pulses/mm / 5 gaps
    assert slider._slot_pulses == 1024
    assert slider.status_code() == 0


def test_position_roundtrip():
    slider = make()
    assert slider.position == 1  # sim powers up homed at 0 pulses
    slider.set_position(4)
    assert slider.position == 4
    # slot 4 = 3 gaps * 1024 pulses, sent as 8 uppercase hex chars
    assert "0ma00000C00" in slider.transport.sent
    assert slider.status() == {"position": 4, "positions": 6}
    assert slider.home() == 1
    assert slider.position == 1


def test_position_bounds():
    slider = make()
    with pytest.raises(ValueError, match="position"):
        slider.set_position(0)
    with pytest.raises(ValueError, match="position"):
        slider.set_position(7)


def test_between_slots_reads_none():
    """A position off any slot center (not homed, mid-move) is unknown,
    not an error — the GUI shows an em dash."""
    slider = make()
    slider.transport.responses["0gp"] = "0PO00000200"  # 512 = half a slot
    assert slider.position is None


def test_negative_position_two_s_complement():
    """Positions slightly behind home come back as two's complement and
    must still resolve to slot 1."""
    slider = make()
    slider.transport.responses["0gp"] = f"0PO{-60 & 0xFFFFFFFF:08X}"
    assert slider.position == 1


def override_move_reply(slider, reply: str) -> None:
    """Replace the sim table's move-absolute handler with a fixed reply."""
    for key in list(slider.transport.responses):
        if getattr(key, "pattern", "").startswith("0ma"):
            slider.transport.responses[key] = reply
            return
    raise AssertionError("no 0ma entry in the sim table")


def test_move_verifies_readback():
    """A move that lands off-target (wrong slot spacing) fails loudly
    instead of silently selecting the wrong filter."""
    slider = make()
    override_move_reply(slider, "0PO00000200")
    with pytest.raises(ResponseError, match="slot_pulses"):
        slider.set_position(2)


def test_move_error_status_raises():
    slider = make()
    override_move_reply(slider, "0GS02")
    with pytest.raises(ResponseError, match="mechanical timeout"):
        slider.set_position(2)


def test_config_slot_pulses_override():
    """slot_pulses from the config block wins over the derived spacing."""
    slider = make(slot_pulses=2048)
    assert slider._slot_pulses == 2048
    slider.set_position(2)
    assert "0ma00000800" in slider.transport.sent


def test_bus_address_prefixes_commands():
    cfg = DeviceConfig(
        key="nd_slider",
        driver="thorlabs_ell12",
        address="COM30",
        options={"bus_address": "2"},
    )
    slider = ThorlabsELL12.from_config(cfg, sim=True)
    # the sim table answers address 0 only; just check the framing
    assert slider._addr == "2"
    with pytest.raises(ValueError, match="bus_address"):
        ThorlabsELL12(slider.transport, "nd_slider", bus_address="25")
