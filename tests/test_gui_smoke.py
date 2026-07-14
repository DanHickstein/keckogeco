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

    def osa_settings(self):
        return {
            "wl_start_nm": 1552.0,
            "wl_stop_nm": 1568.0,
            "resolution_nm": 0.1,
            "sensitivity_dBm": -70.0,
            "sweep_continuous": True,
            "resolutions_nm": [0.06, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0],
        }

    def osa_apply(self, **settings):
        # echo the write back the way the server's read-back does
        base = self.osa_settings()
        base.update(
            {
                "wl_start_nm": settings.get("start_nm", base["wl_start_nm"]),
                "wl_stop_nm": settings.get("stop_nm", base["wl_stop_nm"]),
                "resolution_nm": settings.get("resolution_nm", base["resolution_nm"]),
                "sensitivity_dBm": settings.get("sensitivity_dBm", base["sensitivity_dBm"]),
            }
        )
        return base

    def osa_sweep(self, mode):
        return {"mode": mode, "sweep_continuous": mode == "continuous"}


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


def test_osa_plot_wires_up_when_array_appears(qtbot, tmp_path, monkeypatch):
    """The spectrum panel starts as a placeholder and becomes a live plot
    the first time the server reports the osa_spectrum array."""
    pytest.importorskip("pyqtgraph")
    from keckogeco.gui import prefs
    from keckogeco.gui.mainwindow import MainWindow

    # isolate from the committed prefs file: factory defaults apply
    monkeypatch.setattr(prefs, "GUI_CONFIG_PATH", tmp_path / "gui.toml")
    window = MainWindow(FakeClient())
    qtbot.addWidget(window)
    assert window._osa_plot is None
    window._on_arrays_available(["wsp_profile"])  # OSA still offline
    assert window._osa_plot is None
    window._on_arrays_available(["osa_spectrum", "wsp_profile"])
    assert window._osa_plot is not None
    assert window.poller.array_names == ["osa_spectrum"]
    window._on_array(
        "osa_spectrum",
        {"x": [1550.0, 1560.0], "y": [-40.0, -20.0], "x_label": "nm", "y_label": "dBm"},
    )
    _plot, curve = window._osa_plot
    assert list(curve.getData()[1]) == [-40.0, -20.0]
    # the controls column exists; wiring up pushes the default view to the
    # OSA through the writer thread and populates from the read-back
    controls = window._osa_controls
    assert controls is not None
    assert controls.sensitivity.spin.minimum() == -90.0  # the 86142B's floor
    qtbot.waitUntil(lambda: controls.start.spin.value() == 1550.0)
    qtbot.waitUntil(lambda: "bold" in controls._sweep_buttons["continuous"].styleSheet())
    assert controls.stop.spin.value() == 1570.0
    assert controls.sensitivity.spin.value() == -60.0
    assert controls.resolution.currentData() == 0.06
    # a later settings read-back repopulates the controls
    window._on_call_done("OSA settings", FakeClient().osa_settings())
    assert controls.start.spin.value() == 1552.0
    assert controls.sensitivity.spin.value() == -70.0
    assert controls.resolution.currentData() == 0.1
    # sweep-state indication follows the instrument, single ends stopped
    window._on_call_done("OSA sweep", {"mode": "single", "sweep_continuous": False})
    assert "bold" in controls._sweep_buttons["stop"].styleSheet()
    assert controls._sweep_buttons["continuous"].styleSheet() == ""
    assert controls._sweep_buttons["single"].styleSheet() == ""
    window.poller.stop()
    window.writer.stop()


def test_osa_save_as_default(qtbot, tmp_path, monkeypatch):
    """'Save as default' asks for confirmation, then persists the current
    settings; a fresh controls instance loads them back."""
    from PyQt6.QtWidgets import QMessageBox

    from keckogeco.gui import prefs
    from keckogeco.gui.mainwindow import OsaControls

    monkeypatch.setattr(prefs, "GUI_CONFIG_PATH", tmp_path / "gui.toml")
    controls = OsaControls(lambda **s: None, lambda m: None)
    qtbot.addWidget(controls)
    controls.start.spin.setValue(1548.0)
    controls.stop.spin.setValue(1572.0)
    controls.sensitivity.spin.setValue(-80.0)
    controls.resolution.setCurrentIndex(2)  # 0.2 nm

    # declining leaves everything untouched
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.No)
    )
    controls._save_as_default()
    assert not (tmp_path / "gui.toml").exists()
    assert controls.defaults["start_nm"] == 1550.0

    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    )
    controls._save_as_default()
    assert controls.defaults == {
        "start_nm": 1548.0,
        "stop_nm": 1572.0,
        "resolution_nm": 0.2,
        "sensitivity_dBm": -80.0,
    }
    fresh = OsaControls(lambda **s: None, lambda m: None)
    qtbot.addWidget(fresh)
    assert fresh.defaults == controls.defaults
    assert fresh.start.spin.value() == 1548.0  # controls start at the saved defaults
