"""Laptop tab: health of the machine running the GUI (gui/laptop.py).

The tab is fed by a local sampling thread, not the server; these tests
drive the slot directly with crafted samples (works on any platform),
plus one Windows-only test against the real PDH counters.
"""

import sys
import time

import pytest

pytest.importorskip("PyQt6")

from test_gui_smoke import FakeClient  # noqa: E402

from keckogeco.gui.laptop import (  # noqa: E402
    LaptopHealthError,
    LaptopHealthReader,
    LaptopSample,
    temp_state,
    zone_label,
)


def test_temp_state_bands():
    assert temp_state(75.0) == "ok"
    assert temp_state(84.9) == "ok"
    assert temp_state(85.0) == "warn"
    assert temp_state(94.9) == "warn"
    assert temp_state(95.0) == "hot"


def test_zone_label():
    assert zone_label("\\_TZ.THM0") == "THM0"
    assert zone_label("THM1") == "THM1"


def test_sample_throttling_flag():
    assert not LaptopSample().throttling
    assert not LaptopSample(throttle={"THM0": 0.0}, passive_limit_pct={"THM0": 100.0}).throttling
    assert LaptopSample(throttle={"THM0": 2.0}).throttling
    assert LaptopSample(passive_limit_pct={"THM0": 60.0}).throttling


def _sample(**overrides) -> LaptopSample:
    base = dict(
        zones_C={"THM0": 75.0},
        throttle={"THM0": 0.0},
        passive_limit_pct={"THM0": 100.0},
        cpu_util_pct=12.0,
        cpu_perf_pct=110.0,
        ac_power=True,
        battery_pct=99.0,
    )
    base.update(overrides)
    return LaptopSample(**base)


def test_laptop_tab_updates(qtbot):
    from keckogeco.gui.mainwindow import MainWindow

    window = MainWindow(FakeClient())
    qtbot.addWidget(window)
    # keep real Windows samples from racing the crafted ones below
    window.laptop_poller.stop()
    assert "waiting" in window._laptop_status.text()

    # healthy sample: zone row appears, everything plain, lamp green;
    # the Overview Temperatures panel mirrors the hottest zone
    window._on_laptop_sample(_sample())
    zone = window._laptop_zone_rows["THM0"]
    assert zone.text() == "75.0 °C"
    assert zone.styleSheet() == ""
    assert window._overview_laptop_temp.text() == "75.0 °C"
    assert window._overview_laptop_temp.styleSheet() == ""
    assert window._laptop_throttle.text() == "none"
    assert "#35d07f" in window._laptop_throttle_lamp.styleSheet()
    assert window._laptop_passive.text() == "100 %"
    assert window._laptop_cpu.text() == "12 %"
    assert window._laptop_clock.text() == "110 % of base"
    assert window._laptop_power.text() == "AC (99 %)"
    assert window._laptop_status.isHidden()

    # warm -> amber, hot -> bold red
    window._on_laptop_sample(_sample(zones_C={"THM0": 88.0}))
    assert "#c78a00" in zone.styleSheet()
    window._on_laptop_sample(_sample(zones_C={"THM0": 97.0}))
    assert "#e05252" in zone.styleSheet()
    assert "bold" in zone.styleSheet()
    assert "#e05252" in window._overview_laptop_temp.styleSheet()

    # throttling: red lamp, reasons + passive limit in the text
    window._on_laptop_sample(_sample(throttle={"THM0": 2.0}, passive_limit_pct={"THM0": 60.0}))
    assert "#e05252" in window._laptop_throttle_lamp.styleSheet()
    text = window._laptop_throttle.text()
    assert "THROTTLING" in text
    assert "THM0: 2" in text
    assert "60 %" in text
    assert window._laptop_passive.text() == "60 %"

    # running on battery is an alarm — the comb GUI dies with the battery
    window._on_laptop_sample(_sample(ac_power=False, battery_pct=42.0))
    assert window._laptop_power.text() == "ON BATTERY (42 %)"
    assert "bold" in window._laptop_power.styleSheet()
    window._on_laptop_sample(_sample())
    assert window._laptop_power.text() == "AC (99 %)"
    assert window._laptop_power.styleSheet() == ""

    # a zone showing up later gets its own row (and curve); the Overview
    # readout tracks the hottest zone, not the newest
    window._on_laptop_sample(_sample(zones_C={"THM0": 75.0, "THM1": 40.0}))
    assert window._laptop_zone_rows["THM1"].text() == "40.0 °C"
    assert window._overview_laptop_temp.text() == "75.0 °C"

    # the strip chart accumulated every THM0 sample fed above
    if window._laptop_chart is not None:
        _ages, temps = window._laptop_curves["THM0"].getData()
        assert len(temps) == 7
        assert temps[-1] == 75.0

    # an error sample surfaces the reason instead of stale readouts,
    # and blanks the Overview mirror
    window._on_laptop_sample(LaptopSample(error="laptop health counters are Windows-only"))
    assert "Windows-only" in window._laptop_status.text()
    assert not window._laptop_status.isHidden()
    assert window._overview_laptop_temp.text() == "—"

    window.poller.stop()
    window.writer.stop()


@pytest.mark.skipif(sys.platform != "win32", reason="PDH counters are Windows-only")
def test_reader_reads_real_counters():
    """The PDH plumbing works against the live system. Kept loose: a CI
    VM may expose no thermal zones at all — only shapes and plausible
    ranges are asserted."""
    reader = LaptopHealthReader()
    try:
        first = reader.read()
        time.sleep(0.3)
        second = reader.read()
    finally:
        reader.close()
    assert first.error == ""
    assert second.error == ""
    for temp in second.zones_C.values():
        assert -40.0 < temp < 130.0
    for limit in second.passive_limit_pct.values():
        assert 0.0 <= limit <= 100.0
    if second.cpu_util_pct is not None:
        assert second.cpu_util_pct >= 0.0


@pytest.mark.skipif(sys.platform == "win32", reason="the raise path is non-Windows")
def test_reader_unavailable_off_windows():
    with pytest.raises(LaptopHealthError):
        LaptopHealthReader()
