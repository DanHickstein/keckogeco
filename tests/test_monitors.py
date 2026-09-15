"""TelemetryLogger CSV output and the over-temperature interlock."""

import csv
import math

from keckogeco.comb.keywords import KeywordValue
from keckogeco.comb.monitors import InterlockChannel, TelemetryLogger, TempInterlock


class FakeRegistry:
    def __init__(self, values):
        self._values = values

    def snapshot(self):
        return {k: KeywordValue(v, 0.0) for k, v in self._values.items()}


def read_rows(directory):
    (path,) = directory.glob("*.csv")
    with open(path, newline="") as f:
        return list(csv.reader(f))


def test_log_row_writes_scalars(tmp_path):
    logger = TelemetryLogger(FakeRegistry({"LFC_RFAMP_I": 3.86}), tmp_path)
    logger._log_row()
    header, row = read_rows(tmp_path)
    assert header == ["timestamp", "keyword", "value"]
    assert row[1:] == ["LFC_RFAMP_I", "3.86"]


def test_log_row_expands_arrays_per_channel(tmp_path):
    registry = FakeRegistry(
        {"LFC_TEMP_TEST2": [40.6, 48.2], "LFC_RFAMP_I": 3.86}
    )
    logger = TelemetryLogger(registry, tmp_path)
    logger._log_row()
    rows = read_rows(tmp_path)[1:]
    assert [r[1:] for r in rows] == [
        ["LFC_RFAMP_I", "3.86"],
        ["LFC_TEMP_TEST2[0]", "40.6"],
        ["LFC_TEMP_TEST2[1]", "48.2"],
    ]


def test_log_row_appends_without_second_header(tmp_path):
    logger = TelemetryLogger(FakeRegistry({"LFC_RFAMP_I": 3.86}), tmp_path)
    logger._log_row()
    logger._log_row()
    rows = read_rows(tmp_path)
    assert len(rows) == 3  # one header + two data rows
    assert rows[2][1] == "LFC_RFAMP_I"


# ------------------------------------------------------------ TempInterlock
#
# All timing is driven through _check(now=...) so no test sleeps. The
# channel nominal is 48.2 C with delta 8 -> limit 56.2 C, hold 30 s.


class FakeSensor:
    def __init__(self, temp_C):
        self.temp_C = temp_C

    def __call__(self):
        return self.temp_C


def make_interlock(sensor, trips):
    return TempInterlock(
        [InterlockChannel("RF amplifier", sensor, nominal_C=48.2)],
        trips.append,
        delta_C=8.0,
        hold_s=30.0,
    )


def test_interlock_quiet_at_nominal():
    trips = []
    interlock = make_interlock(FakeSensor(48.2), trips)
    for now in range(0, 120, 5):
        interlock._check(now)
    assert trips == []
    assert interlock.tripped is False


def test_interlock_no_trip_before_hold():
    trips = []
    interlock = make_interlock(FakeSensor(60.0), trips)
    for now in range(0, 30, 5):  # 25 s of over-temperature: not enough
        interlock._check(now)
    assert trips == []


def test_interlock_trips_after_hold_once():
    trips = []
    interlock = make_interlock(FakeSensor(60.0), trips)
    for now in range(0, 65, 5):
        interlock._check(now)
    assert len(trips) == 1  # latched: no re-fire while still hot
    assert interlock.tripped is True
    assert "RF amplifier" in trips[0]
    assert "60.0" in trips[0]


def test_interlock_dip_below_resets_the_timer():
    trips = []
    sensor = FakeSensor(60.0)
    interlock = make_interlock(sensor, trips)
    for now in range(0, 30, 5):
        interlock._check(now)
    sensor.temp_C = 50.0  # back under the limit for one sample
    interlock._check(30)
    sensor.temp_C = 60.0
    for now in range(35, 60, 5):  # only 25 s over since the dip
        interlock._check(now)
    assert trips == []
    interlock._check(65)
    assert len(trips) == 1


def test_interlock_nan_never_trips_and_resets():
    trips = []
    sensor = FakeSensor(math.nan)
    interlock = make_interlock(sensor, trips)
    for now in range(0, 65, 5):
        interlock._check(now)
    assert trips == []
    sensor.temp_C = 60.0
    for now in range(65, 90, 5):
        interlock._check(now)
    sensor.temp_C = math.nan  # open thermocouple mid-episode: timer resets
    interlock._check(90)
    sensor.temp_C = 60.0
    for now in range(95, 120, 5):
        interlock._check(now)
    assert trips == []


def test_interlock_read_failure_is_nan():
    trips = []

    def broken_read():
        raise OSError("USB gone")

    interlock = TempInterlock(
        [InterlockChannel("Pritel", broken_read, nominal_C=33.9)], trips.append
    )
    for now in range(0, 65, 5):
        interlock._check(now)
    assert trips == []
    assert interlock.info()["channels"][0]["temperature_C"] is None


def test_interlock_rearms_after_recovery_and_can_trip_again():
    trips = []
    sensor = FakeSensor(60.0)
    interlock = make_interlock(sensor, trips)
    for now in range(0, 35, 5):
        interlock._check(now)
    assert len(trips) == 1
    sensor.temp_C = 48.2
    interlock._check(40)
    assert interlock.tripped is False  # cooled down: re-armed
    sensor.temp_C = 60.0
    for now in range(45, 80, 5):
        interlock._check(now)
    assert len(trips) == 2


def test_interlock_shutdown_error_still_latches():
    def failing_shutdown(reason):
        raise RuntimeError("PSU offline")

    sensor = FakeSensor(60.0)
    interlock = TempInterlock(
        [InterlockChannel("RF amplifier", sensor, nominal_C=48.2)], failing_shutdown
    )
    try:
        for now in range(0, 35, 5):
            interlock._check(now)
    except RuntimeError:
        pass  # MonitorThread.run would log this and carry on
    assert interlock.tripped is True  # latched before the callback ran


def test_interlock_info_shape():
    trips = []
    interlock = make_interlock(FakeSensor(49.0), trips)
    interlock._check(0)
    info = interlock.info()
    assert info["tripped"] is False
    assert info["trip_reason"] is None
    assert info["last_trip"] is None
    assert info["delta_C"] == 8.0
    assert info["hold_s"] == 30.0
    (channel,) = info["channels"]
    assert channel["name"] == "RF amplifier"
    assert channel["nominal_C"] == 48.2
    assert channel["limit_C"] == 56.2
    assert channel["temperature_C"] == 49.0
    assert channel["over_s"] is None  # under the limit: no countdown


def test_interlock_over_s_counts_up():
    trips = []
    interlock = make_interlock(FakeSensor(60.0), trips)
    interlock._check(0)
    assert interlock._over_since == {"RF amplifier": 0}
    interlock._check(10)
    assert interlock._over_since == {"RF amplifier": 0}  # first-over time sticks


def test_interlock_trip_records_details_and_tracks_peak():
    trips = []
    sensor = FakeSensor(60.0)
    interlock = make_interlock(sensor, trips)
    for now in range(0, 35, 5):
        interlock._check(now)
    (channel,) = interlock.info()["last_trip"]["channels"]
    assert channel == {"name": "RF amplifier", "max_temp_C": 60.0, "limit_C": 56.2}
    sensor.temp_C = 63.7  # still rising after the shutdown: peak follows
    interlock._check(40)
    (channel,) = interlock.info()["last_trip"]["channels"]
    assert channel["max_temp_C"] == 63.7
    sensor.temp_C = 48.2  # cooled: the latch re-arms but the message stays
    interlock._check(45)
    assert interlock.tripped is False
    assert interlock.info()["last_trip"] is not None


def test_interlock_clear_trip_restarts_the_countdown():
    trips = []
    sensor = FakeSensor(60.0)
    interlock = make_interlock(sensor, trips)
    for now in range(0, 35, 5):
        interlock._check(now)
    assert len(trips) == 1
    interlock.clear_trip()  # operator turned the Pritel / RF amp back on
    assert interlock.tripped is False
    assert interlock.info()["last_trip"] is None
    for now in range(40, 65, 5):  # still hot: fresh hold, no instant re-trip
        interlock._check(now)
    assert len(trips) == 1
    interlock._check(70)  # 30 s after the clear-time restart
    assert len(trips) == 2
