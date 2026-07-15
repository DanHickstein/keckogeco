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

    def interlock(self):
        return {
            "voltage_V": 2.44,
            "low_threshold_V": 1.47,
            "high_threshold_V": 4.4,
            "ok_to_amplify": True,
            "resettable": False,
        }


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

    # --- save the live spectrum: dialog prefilled with a datetime name
    from PyQt6.QtWidgets import QFileDialog

    csv_path = tmp_path / "spec.csv"
    prefill = {}

    def fake_save(_parent, _caption, directory, _filter):
        prefill["directory"] = directory
        return str(csv_path), "csv"

    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(fake_save))
    window._osa_save()
    assert "osa_20" in prefill["directory"]  # osa_<date-time>.csv suggestion
    text = csv_path.read_text(encoding="utf-8")
    assert "# resolution_nm: 0.1" in text  # metadata header rides along
    assert "1550,-40" in text

    # --- load it back as the reference: overlay curve + persisted path
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(csv_path), "csv"))
    )
    window._osa_load("reference")
    assert list(window._osa_curves["reference"].getData()[0]) == [1550.0, 1560.0]
    # stacking: reference behind loaded behind the live trace
    _plot, live_curve = window._osa_plot
    assert window._osa_curves["reference"].zValue() < live_curve.zValue()
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(csv_path), "csv"))
    )
    window._osa_load("loaded")
    assert (
        window._osa_curves["reference"].zValue()
        < window._osa_curves["loaded"].zValue()
        < live_curve.zValue()
    )
    # a fresh GUI restores the reference at wire-up
    window2 = MainWindow(FakeClient())
    qtbot.addWidget(window2)
    window2._on_arrays_available(["osa_spectrum"])
    assert list(window2._osa_curves["reference"].getData()[1]) == [-40.0, -20.0]
    window2.poller.stop()
    window2.writer.stop()

    window.poller.stop()
    window.writer.stop()


def test_im_scan_panel_wires_up_when_array_appears(qtbot, tmp_path, monkeypatch):
    """The IM Bias Lock tab starts as a placeholder and becomes a live
    plot + controls when the server offers the im_scan array."""
    pytest.importorskip("pyqtgraph")
    from keckogeco.gui import prefs
    from keckogeco.gui.mainwindow import MainWindow

    monkeypatch.setattr(prefs, "GUI_CONFIG_PATH", tmp_path / "gui.toml")
    window = MainWindow(FakeClient())
    qtbot.addWidget(window)
    assert window._im_plot is None
    window._on_arrays_available(["im_scan"])
    assert window._im_plot is not None
    assert "im_scan" in window.poller.array_names
    controls = window._im_controls
    assert controls is not None
    assert controls.params() == {
        "v_start": -2.0,
        "v_stop": 1.0,
        "v_step": 0.02,
        "settle_s": 0.2,
    }
    # the manual bias + RF attenuator keyword controls live in the column
    assert window.widgets["LFC_IM_BIAS"] is not None
    assert window.widgets["LFC_IM_RF_ATT"] is not None

    # mid-scan payload: curve + readouts update, buttons flip to running,
    # and the array poll drops to every cycle for a live plot
    window._on_array(
        "im_scan",
        {"x": [-2.0, -1.98], "y": [0.1, 0.2], "running": True},
    )
    _plot, curve = window._im_plot
    assert list(curve.getData()[1]) == [0.1, 0.2]
    assert not controls.scan_button.isEnabled()
    assert controls.abort_button.isEnabled()
    assert "0.2000 V" in controls.input_v.text()  # last recorded point
    assert window.poller.array_every["im_scan"] == 1

    # idle payload carries the live servo readouts; poll cadence relaxes
    window._on_array(
        "im_scan",
        {"x": [], "y": [], "running": False, "mode": "MAN", "input_V": 0.42, "bias_V": 0.5},
    )
    assert controls.scan_button.isEnabled()
    assert controls.mode.text() == "MANUAL"
    assert "0.4200 V" in controls.input_v.text()
    assert window.poller.array_every["im_scan"] == 3

    # the IM tab mirrors its own actions' progress, not the transitions'
    window._on_state(
        {
            "state": "STANDBY",
            "subsystems": {},
            "action": {"name": "im_bias_scan", "running": True, "step": 3, "message": "x"},
        }
    )
    assert "im_bias_scan" in window.im_action_label.text()
    window._on_state(
        {
            "state": "STANDBY",
            "subsystems": {},
            "action": {"name": "set_standby", "running": True, "step": 1, "message": "y"},
        }
    )
    assert window.im_action_label.text() == ""

    window.poller.stop()
    window.writer.stop()


def test_keyword_spinbox_typed_value_survives_polls(qtbot):
    """A submitted value is not snapped back by the next poll refresh
    while the write is in flight; a rejected write releases the hold so
    the instrument's value returns. (Focus can't be exercised offscreen,
    so the edit state is driven directly.)"""
    from keckogeco.gui.widgets import KeywordSpinBox

    submitted = []
    spec = {"units": "V", "min": -3.0, "max": 3.0, "help": "test"}
    widget = KeywordSpinBox("LFC_IM_BIAS", spec, lambda k, v: submitted.append((k, v)))
    qtbot.addWidget(widget)
    widget.spin.setValue(1.25)
    widget._editing = True  # what _on_user_change sets on a focused keystroke
    widget._apply()  # what editingFinished (Enter / focus-out) triggers
    assert submitted == [("LFC_IM_BIAS", 1.25)]

    widget.update_value(0.0)  # stale poll value: held off during the grace
    assert widget.spin.value() == 1.25
    widget.update_value(1.25)  # the write landed in the cache: accepted
    assert widget.spin.value() == 1.25

    # a refused write (e.g. during a running action) releases the hold
    widget.spin.setValue(2.5)
    widget._editing = True
    widget._apply()
    widget.write_rejected()
    widget.update_value(1.25)
    assert widget.spin.value() == 1.25


def test_interlock_voltage_coloring(qtbot):
    """The trip window populates from /interlock and the live voltage
    turns green inside the window, red outside."""
    from keckogeco.gui.mainwindow import MainWindow

    window = MainWindow(FakeClient())
    qtbot.addWidget(window)
    window._on_call_done("interlock", FakeClient().interlock())
    assert "1.47" in window._interlock_threshold.text()
    assert "4.40" in window._interlock_threshold.text()

    def volts(value):
        window._on_keywords({"LFC_PTAMP_INTERLOCK_V": {"value": value}})
        return window.widgets["LFC_PTAMP_INTERLOCK_V"].styleSheet()

    assert "#35d07f" in volts(2.44)  # in window -> green
    assert "#e05252" in volts(0.3)  # below -> red
    assert "#e05252" in volts(4.9)  # above -> red
    assert volts(None) == ""  # unknown -> plain
    window.poller.stop()
    window.writer.stop()


def test_wsp_panel_remembers_values(qtbot, tmp_path, monkeypatch):
    """The WaveShaper boxes start at the commissioned 2.14 / 0 / 1559.8,
    and an edit persists into the prefs so a fresh GUI restores it."""
    from keckogeco.gui import prefs
    from keckogeco.gui.mainwindow import MainWindow

    monkeypatch.setattr(prefs, "GUI_CONFIG_PATH", tmp_path / "gui.toml")
    window = MainWindow(FakeClient())
    qtbot.addWidget(window)
    assert window._wsp_spins["LFC_WSP_PHASE"].spin.value() == 2.14
    assert window._wsp_spins["LFC_WSP_TOD"].spin.value() == 0.0
    assert window._wsp_spins["LFC_WSP_CENTER"].spin.value() == 1559.8
    # user edits GDD -> saved; a fresh GUI comes up with the edited trio
    window._wsp_spins["LFC_WSP_PHASE"].spin.setValue(3.1)
    window._wsp_submit("LFC_WSP_PHASE", 3.1)
    window2 = MainWindow(FakeClient())
    qtbot.addWidget(window2)
    assert window2._wsp_spins["LFC_WSP_PHASE"].spin.value() == 3.1
    assert window2._wsp_spins["LFC_WSP_CENTER"].spin.value() == 1559.8
    for w in (window, window2):
        w.poller.stop()
        w.writer.stop()


def test_edfa23_current_in_mA_and_remembered(qtbot, tmp_path, monkeypatch):
    """The EDFA23 box is a pump current in mA (the unit runs in ACC) with
    room for the commissioned 80; an edit persists into the prefs so a
    fresh GUI pre-fills it. A live poll value still wins over the memory."""
    from keckogeco.gui import prefs
    from keckogeco.gui.mainwindow import MainWindow

    monkeypatch.setattr(prefs, "GUI_CONFIG_PATH", tmp_path / "gui.toml")
    window = MainWindow(FakeClient())
    qtbot.addWidget(window)
    box = window.widgets["LFC_EDFA23_P"]
    assert box.spin.suffix() == " mA"
    assert box.spin.maximum() == 1500.0
    # user sets the recommended 80 mA -> saved; a fresh GUI pre-fills it
    box.spin.setValue(80.0)
    window._edfa23_submit("LFC_EDFA23_P", 80.0)
    window2 = MainWindow(FakeClient())
    qtbot.addWidget(window2)
    box2 = window2.widgets["LFC_EDFA23_P"]
    assert box2.spin.value() == 80.0
    # the memory is display-only: the instrument's live value overwrites it
    window2._on_keywords({"LFC_EDFA23_P": {"value": 0.0}})
    assert box2.spin.value() == 0.0
    for w in (window, window2):
        w.poller.stop()
        w.writer.stop()


def test_self_destruct_countdown(qtbot):
    """Fully armed and completely harmless."""
    from keckogeco.gui.mainwindow import SelfDestructDialog

    dialog = SelfDestructDialog()
    qtbot.addWidget(dialog)
    assert dialog.label.text() == "The system will self-destruct in 5 seconds."
    for _ in range(5):
        dialog._tick()
    assert "kidding" in dialog.label.text()
    assert "Steph" in dialog.label.text()


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
