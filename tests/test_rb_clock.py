"""Tests for the FS725 Rb clock driver."""

import pytest

from keckogeco.config import DeviceConfig
from keckogeco.drivers.rb_clock import RbClock
from keckogeco.drivers.transports import SimTransport


def test_lock_status_sim():
    inst = RbClock.from_config(
        DeviceConfig(key="rb_clock", driver="rb_clock", address="ASRL9::INSTR"), sim=True
    )
    with inst:
        assert inst.phase_locked is True
        assert inst.frequency_locked is True
        assert inst.serial_number == "12345"
        assert inst.status() == {"phase_locked": True, "frequency_locked": True}


def test_bad_reply_raises():
    from keckogeco.drivers.errors import ResponseError

    inst = RbClock(SimTransport({"PL?": "garbage"}), "rb")
    inst.connect()
    with pytest.raises(ResponseError, match="phase-lock"):
        _ = inst.phase_locked
