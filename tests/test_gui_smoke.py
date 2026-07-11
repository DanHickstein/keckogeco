"""GUI smoke test: the main window constructs and populates against a fake
client (no server, no network, offscreen rendering)."""

import pytest

pytest.importorskip("PyQt6")

from keckogeco.comb.keywords import load_schema  # noqa: E402


class FakeClient:
    """Stands in for KeckogecoClient; returns schema + canned data."""

    base_url = "fake://"

    def schema(self):
        schema = load_schema()
        return {
            name: {
                "type": s.type,
                "writable": s.writable,
                "units": s.units,
                "min": s.min,
                "max": s.max,
                "enum": s.enum,
                "help": s.help,
                "bound": True,
            }
            for name, s in schema.items()
        }

    def snapshot(self):
        return {
            "LFC_EDFA27_P": {"value": 150.0, "timestamp": 0, "type": "double", "units": "mW"},
            "LFC_PTAMP_ONOFF": {"value": False, "timestamp": 0, "type": "boolean", "units": ""},
            "LFC_T_RACK_TOP": {"value": 23.5, "timestamp": 0, "type": "double", "units": "C"},
        }

    def state(self):
        return {
            "state": "STANDBY",
            "legacy_code": 15015,
            "subsystems": {"ptamp": False, "edfa27": True, "rf_oscillator": True},
        }

    def health(self):
        return {"status": "ok"}

    def write(self, name, value):
        return {"name": name, "value": value}


def test_mainwindow_constructs_and_updates(qtbot):
    from keckogeco.gui.mainwindow import MainWindow

    window = MainWindow(FakeClient())
    qtbot.addWidget(window)
    # widgets exist for the tier-1 keywords
    assert "LFC_EDFA27_P" in window.widgets
    assert "LFC_PTAMP_ONOFF" in window.widgets
    # feed data through the same slots the poll thread uses
    window._on_keywords(FakeClient().snapshot())
    window._on_state(FakeClient().state())
    assert window.state_banner.text() == "STANDBY"
    assert "150" in window.widgets["LFC_EDFA27_P"].spin.text()
    window.poller.stop()
    window.writer.stop()
