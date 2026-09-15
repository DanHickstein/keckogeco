"""TelemetryLogger CSV output, including array-keyword expansion."""

import csv

from keckogeco.comb.keywords import KeywordValue
from keckogeco.comb.monitors import TelemetryLogger


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
