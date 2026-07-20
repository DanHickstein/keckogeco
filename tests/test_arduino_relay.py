"""Tests for the Arduino interlock relay driver."""

import pytest

from keckogeco.config import DeviceConfig
from keckogeco.drivers.arduino_relay import ArduinoRelay, RelayStatus


@pytest.fixture
def relay():
    cfg = DeviceConfig(key="arduino_relay", driver="arduino_relay", address="COM3")
    inst = ArduinoRelay.from_config(cfg, sim=True)
    inst.connect()
    return inst


def test_default_transport_is_serial():
    cfg = DeviceConfig(key="arduino_relay", driver="arduino_relay", address="COM3")
    inst = ArduinoRelay.from_config(cfg)
    from keckogeco.drivers.transports import SerialTransport

    assert isinstance(inst.transport, SerialTransport)
    assert inst.transport.baud_rate == 9600
    # opening with DTR asserted auto-resets the Uno, tripping the
    # interlock latch on every server start — must stay suppressed
    assert inst.transport.dtr is False
    assert inst.transport.rts is False


def test_relay_status_parsing(relay):
    status = relay.relay_status()
    assert status.low_threshold == 300
    assert status.high_threshold == 900
    assert status.voltage_now == 500
    assert status.ok_to_amplify is True


def test_ok_to_amplify_logic():
    ok = RelayStatus(300, 900, 500, 500, 500)
    assert ok.ok_to_amplify and not ok.resettable
    tripped_but_recoverable = RelayStatus(300, 900, 200, 200, 500)
    assert not tripped_but_recoverable.ok_to_amplify
    assert tripped_but_recoverable.resettable
    assert "reset" in tripped_but_recoverable.describe()
    low_power = RelayStatus(300, 900, 200, 200, 100)
    assert not low_power.ok_to_amplify and not low_power.resettable
    assert "too low" in low_power.describe()
    high_power = RelayStatus(300, 900, 950, 950, 1000)
    assert "too high" in high_power.describe()


def test_yj_shutter(relay):
    assert relay.yj_open is True
    relay.close_yj()
    assert relay.yj_open is False
    relay.open_yj()
    assert relay.yj_open is True


def test_threshold_guards(relay):
    relay.set_low_threshold(300)
    with pytest.raises(ValueError, match="force=True"):
        relay.set_low_threshold(50)
    relay.set_low_threshold(50, force=True)  # explicit override allowed
    with pytest.raises(ValueError, match="force=True"):
        relay.set_high_threshold(950)
    with pytest.raises(ValueError, match="outside"):
        relay.set_high_threshold(2000)


def test_status_dict(relay):
    status = relay.status()
    assert status["ok_to_amplify"] is True
    assert status["yj_open"] is True
    assert "OK_to_Amplify" in status["description"]
