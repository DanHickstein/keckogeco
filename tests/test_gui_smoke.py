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
            # rack DAQ: ch6 hot (baseline 26.0), ch7 permanently open (null)
            "LFC_TEMP_TEST1": {
                "value": [28.5, 26.0, 27.1, 26.3, 21.0, 14.0, 41.9, None],
                "timestamp": 0,
                "type": "double array",
                "units": "",
            },
            # table DAQ: RF amp (ch1) at its hot-by-design baseline of 48,
            # glycol out (ch4) well below its 15.6 baseline
            "LFC_TEMP_TEST2": {
                "value": [40.3, 48.1, 32.0, 28.2, 11.0, 34.5, 23.0, 24.0],
                "timestamp": 0,
                "type": "double array",
                "units": "",
            },
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

    def im_apply(self, **settings):
        # read-back of the servo state (empty call = read; gains included)
        return {
            "mode": "MAN",
            "setpoint_V": 0.41,
            "bias_V": 0.5,
            "input_V": 0.42,
            "prop_gain": -2.0,
            "intg_gain": 0.1,
            **settings,
        }


def test_pendulum_rep_rate_display(qtbot):
    """The Clock tab shows the measured rep rate with all the digits the
    CNT-90XL earns, plus a delta-from-16-GHz line; NaN arrives as null
    (RF chain off) and shows an em dash."""
    from keckogeco.gui.mainwindow import MainWindow

    window = MainWindow(FakeClient())
    qtbot.addWidget(window)
    display = window.widgets["LFC_REPRATE"]
    assert display.text() == "—"
    window._on_keywords({"LFC_REPRATE": {"value": 16000000000.12}})
    assert "16" in display.text() and "Hz" in display.text()
    assert "&thinsp;000" in display.text()  # digit grouping, all 13 digits
    assert "+0.12" in display.text()  # Δ from 16 GHz
    window._on_keywords({"LFC_REPRATE": {"value": None}})
    assert display.text() == "—"
    window.poller.stop()
    window.writer.stop()


def test_clock_tab_reference_chain(qtbot):
    """The Clock tab surfaces the reference chain: FS725 lock lamps, and
    a counter-timebase lamp that is green ONLY on EXT — a counter on its
    internal timebase read ~200 Hz off 16 GHz with everything else green
    (2026-07-17), which is exactly what these exist to catch."""
    from keckogeco.gui.mainwindow import MainWindow

    window = MainWindow(FakeClient())
    qtbot.addWidget(window)
    phase = window.widgets["LFC_RBCLOCK_PHASELOCK"]
    freq = window.widgets["LFC_RBCLOCK_FREQLOCK"]
    ref = window.widgets["LFC_REPRATE_REF"]

    window._on_keywords(
        {
            "LFC_RBCLOCK_PHASELOCK": {"value": True},
            "LFC_RBCLOCK_FREQLOCK": {"value": True},
            "LFC_REPRATE_REF": {"value": "EXT"},
        }
    )
    assert "#35d07f" in phase.lamp.styleSheet()  # locked -> green
    assert "#35d07f" in freq.lamp.styleSheet()
    assert "#35d07f" in ref.lamp.styleSheet()  # external 10 MHz -> green
    assert ref.display.text() == "EXT"

    # the failure signatures: Rb unlocked, counter on internal timebase
    window._on_keywords(
        {
            "LFC_RBCLOCK_PHASELOCK": {"value": False},
            "LFC_REPRATE_REF": {"value": "INT"},
        }
    )
    assert "#3a4350" in phase.lamp.styleSheet()  # unlocked -> grey
    assert "#3a4350" in ref.lamp.styleSheet()  # internal timebase -> grey
    assert ref.display.text() == "INT"

    window.poller.stop()
    window.writer.stop()


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

    # every thermocouple channel renders from the LFC_TEMP_TEST arrays,
    # colored against its normal-operation baseline (±3 C band; the
    # baselines are the 2026-07-19 door-closed + Pritel-on condition)
    rack = {ch: cell for ch, _base, cell in window.widgets["LFC_TEMP_TEST1"]._cells}
    assert len(rack) == 7  # ch7 (permanently unconnected) is not shown
    assert rack[0].text() == "28.50 °C"
    # 28.5 was normal with the door open; vs the 35.4 operating baseline
    # it reads cold -> blue bold
    assert "#5b9bd5" in rack[0].styleSheet()
    assert "bold" in rack[0].styleSheet()
    assert "#e05252" in rack[6].styleSheet()  # 41.9 vs 34.3 baseline -> red
    assert "bold" in rack[6].styleSheet()
    table = {ch: cell for ch, _base, cell in window.widgets["LFC_TEMP_TEST2"]._cells}
    assert len(table) == 8
    assert table[1].text() == "48.10 °C"
    assert table[1].styleSheet() == ""  # RF amp: ~48 C is its normal baseline
    assert "#5b9bd5" in table[4].styleSheet()  # 11.0 vs 15.9 baseline -> blue
    assert "bold" in table[4].styleSheet()

    # a board going away (keyword drops to null) blanks its readouts
    window._on_keywords({"LFC_TEMP_TEST2": {"value": None}})
    assert table[0].text() == "—"
    assert table[0].styleSheet() == ""
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
    """The IM Bias Lock tab starts as placeholders and becomes the
    servo panel + strip charts, the scan panel, and the OSA mirror when
    the server offers the im_scan / osa_spectrum arrays."""
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
    assert window.poller.array_every["im_scan"] == 1  # strip charts sample ~1 Hz
    controls = window._im_controls
    assert controls is not None
    # the suggestion box starts with resting text, so its space is
    # reserved and a finished scan doesn't reflow the tab
    assert controls.recommend.text() == controls.RECOMMEND_PLACEHOLDER
    assert controls.params() == {
        "v_start": -5.0,
        "v_stop": 5.0,
        "v_step": 0.2,
        "settle_s": 1.0,
    }
    # bias + RF attenuator keyword controls live in the servo panel (top);
    # the bias box is deliberately NOT keyword-registered — the snapshot
    # reports MOUT while the array reports the live OMON, and feeding the
    # box from both made it flip between the two while locked
    servo = window._im_servo_panel
    assert servo is not None
    assert servo.bias is not None
    assert "LFC_IM_BIAS" not in window.widgets
    assert window.widgets["LFC_IM_RF_ATT"] is not None
    assert len(window._im_charts) == 2  # photodiode + bias strip charts

    # mid-scan payload: curve + readouts update, buttons flip to running
    window._on_array(
        "im_scan",
        {"x": [-2.0, -1.98], "y": [0.1, 0.2], "running": True},
    )
    _plot, curve = window._im_plot
    assert list(curve.getData()[1]) == [0.1, 0.2]
    assert not controls.scan_button.isEnabled()
    assert controls.abort_button.isEnabled()
    assert "0.2000 V" in servo.input_v.text()  # last recorded point
    assert "scanning" in servo.status.text()
    # the purple dot follows the sweep: the newest point is the position
    dot_x, dot_y = window._im_pos_dot.getData()
    assert list(dot_x) == [-1.98]
    assert list(dot_y) == [0.2]

    # a finished scan (running True -> False edge) shows suggested lock
    # settings as TEXT (the operator enters them in the servo panel),
    # picking the mid-fringe crossing nearest the Bias start box (0 V)
    import math

    sweep_x = [round(-5.0 + 0.1 * i, 3) for i in range(100)]
    sweep_y = [0.5 * math.sin(2 * x + 1) for x in sweep_x]
    window._on_array("im_scan", {"x": sweep_x, "y": sweep_y, "running": False, "mode": "MAN"})
    suggestion = controls.recommend.text()
    assert "suggest:" in suggestion
    assert "P " in suggestion and "I 0.1" in suggestion  # slope ±1 -> P ±2.0
    assert "bias -0.500" in suggestion  # sin(2v+1) crossing nearest 0 V
    assert "slope" not in suggestion  # kept short deliberately

    # PI gain boxes populate from an /im read-back (not the polled array)
    window._on_call_done(
        "IM settings",
        {"mode": "MAN", "setpoint_V": 0.41, "prop_gain": -2.0, "intg_gain": 0.1},
    )
    assert servo.prop.spin.value() == -2.0
    assert servo.intg.spin.value() == 0.1

    # the reference calibration overlay restores from prefs (OSA pattern)
    from keckogeco.gui import spectra as spectra_mod

    ref_csv = tmp_path / "im_ref.csv"
    spectra_mod.save_spectrum_csv(ref_csv, [-1.0, 1.0], [0.2, 0.8], {})
    prefs.save_section("im_reference", {"csv": ref_csv.as_posix()})
    assert window._im_ref_curve is None
    window._im_restore_reference()
    assert list(window._im_ref_curve.getData()[1]) == [0.2, 0.8]

    # idle payload: servo readouts + strip-chart history; scan re-enabled
    window._on_array(
        "im_scan",
        {
            "x": [],
            "y": [],
            "running": False,
            "mode": "MAN",
            "input_V": 0.42,
            "bias_V": 0.5,
            "setpoint_V": 0.41,
        },
    )
    assert controls.scan_button.isEnabled()
    assert servo.status.text() == "Unlocked"
    assert servo.lock_button.text() == "Lock"
    assert "0.4200 V" in servo.input_v.text()
    assert servo.setpoint.spin.value() == 0.41
    # the Bias out box follows the live output and stays editable
    assert servo.bias.spin.value() == 0.5
    assert servo.bias.isEnabled()
    assert window._im_history["pd"] == [0.42]
    assert window._im_history["bias"] == [0.5]
    # the purple dot marks the current (bias, photodiode) on the curve
    dot_x, dot_y = window._im_pos_dot.getData()
    assert list(dot_x) == [0.5]
    assert list(dot_y) == [0.42]

    # locked: scan blocked (server would 409 anyway), status row + gray
    # read-only bias box show it; the lockpoint stays adjustable
    window._on_array(
        "im_scan",
        {"x": [], "y": [], "running": False, "mode": "PID", "input_V": 0.41, "bias_V": 0.48},
    )
    assert not controls.scan_button.isEnabled()
    assert "unlock" in controls.scan_button.toolTip()
    assert servo.status.text() == "Locked"
    assert servo.lock_button.text() == "Unlock"
    assert not servo.bias.isEnabled()
    assert servo.setpoint.isEnabled()

    # the one button toggles: locked -> unlock; unlocked -> write the
    # Bias start to LFC_IM_BIAS, then engage (FIFO write queue)
    submitted = []
    monkeypatch.setattr(window, "_submit", lambda k, v: submitted.append((k, v)))
    servo.lock_button.click()
    assert submitted == [("LFC_IM_LOCK_MODE", "0")]
    window._on_array("im_scan", {"x": [], "y": [], "running": False, "mode": "MAN"})
    assert servo.bias.isEnabled()
    submitted.clear()
    servo.bias_start.spin.setValue(-0.5)
    servo.lock_button.click()
    assert submitted == [("LFC_IM_BIAS", -0.5), ("LFC_IM_LOCK_MODE", "1")]

    # the OSA mirror at the bottom follows the same spectrum array
    window._on_arrays_available(["im_scan", "osa_spectrum"])
    assert window._im_osa_plot is not None
    window._on_array("osa_spectrum", {"x": [1550.0, 1560.0], "y": [-40.0, -20.0]})
    _plot, osa_curve = window._im_osa_plot
    assert list(osa_curve.getData()[1]) == [-40.0, -20.0]

    # ALL action progress goes to the status bar (the comb-state strip
    # has no text area); IM scans additionally show in the scan panel's
    # suggestion box while running
    window._on_state(
        {
            "state": "STANDBY",
            "subsystems": {},
            "action": {"name": "im_bias_scan", "running": True, "step": 3, "message": "x"},
        }
    )
    assert "im_bias_scan" in controls.recommend.text()
    assert "im_bias_scan" in window.statusBar().currentMessage()
    window._on_state(
        {
            "state": "STANDBY",
            "subsystems": {},
            "action": {"name": "set_standby", "running": True, "step": 1, "message": "y"},
        }
    )
    assert "set_standby" in window.statusBar().currentMessage()

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


def test_keyword_spinbox_live_applies_arrow_steps(qtbot):
    """``live=True`` (the IM servo boxes, issue #41): arrow/wheel steps
    submit after a short pause instead of waiting for Enter/focus-out.
    (Focus can't be exercised offscreen, so the edit state + debounce
    start are driven directly, as _on_user_change would on a step.)"""
    from keckogeco.gui.widgets import KeywordSpinBox

    submitted = []
    spec = {"units": "V", "min": -8.0, "max": 8.0, "step": 0.01}
    widget = KeywordSpinBox("LFC_IM_BIAS", spec, lambda k, v: submitted.append((k, v)), live=True)
    qtbot.addWidget(widget)
    # typing must not submit per keystroke: keyboard tracking is off, so
    # keystrokes never reach valueChanged (only Enter/focus-out do)
    assert not widget.spin.keyboardTracking()
    widget.spin.setValue(1.25)
    widget._editing = True
    widget._debounce.start()
    qtbot.waitUntil(lambda: submitted == [("LFC_IM_BIAS", 1.25)], timeout=2000)
    # editingFinished right after the debounce fired must not double-submit
    widget._apply()
    assert submitted == [("LFC_IM_BIAS", 1.25)]
    # the submitted value is protected from poll snap-back like any write
    widget.update_value(0.0)
    assert widget.spin.value() == 1.25


def test_im_scan_blocked_while_pritel_on(qtbot, tmp_path, monkeypatch):
    """Issue #43: starting a bias scan with the Pritel pumping pops a
    dialog and never reaches the server (which would refuse it too)."""
    pytest.importorskip("pyqtgraph")
    from PyQt6.QtWidgets import QMessageBox

    from keckogeco.gui import prefs
    from keckogeco.gui.mainwindow import MainWindow

    monkeypatch.setattr(prefs, "GUI_CONFIG_PATH", tmp_path / "gui.toml")
    window = MainWindow(FakeClient())
    qtbot.addWidget(window)
    window._on_arrays_available(["im_scan"])

    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kw: warnings.append(args))
    calls = []
    monkeypatch.setattr(window.writer, "submit_call", lambda label, fn: calls.append(label))

    window._on_keywords({"LFC_PTAMP_ONOFF": {"value": True, "timestamp": 0}})
    window._im_scan_start()
    assert len(warnings) == 1 and "Pritel" in warnings[0][2]
    assert calls == []

    window._on_keywords({"LFC_PTAMP_ONOFF": {"value": False, "timestamp": 0}})
    window._im_scan_start()
    assert calls == ["IM scan"]

    # a server-side refusal (stale snapshot race) also pops a dialog
    window._on_write_failed("IM scan", "the Pritel amplifier is ON; ...")
    assert len(warnings) == 2

    window.poller.stop()
    window.writer.stop()


def test_keyword_spinbox_readback_keeps_setpoint(qtbot):
    """With a readback label the box is a setpoint: polls feed the label
    and never move the box (the Pritel currents read 0 until emission is
    on, and that must not erase the value about to be applied)."""
    from keckogeco.gui.widgets import KeywordSpinBox

    spec = {"units": "mA", "min": 0.0, "max": 600.0, "help": "preamp current"}
    widget = KeywordSpinBox("LFC_PTAMP_PRE_P", spec, lambda k, v: None, readback=True)
    qtbot.addWidget(widget)
    widget.spin.setValue(600.0)

    widget.update_value(0.0)
    assert widget.spin.value() == 600.0  # setpoint stands
    assert widget.readback.text() == "0.000 mA"  # measurement shown beside it
    widget.update_value(None)
    assert widget.readback.text() == "—"


def test_pritel_panel_setpoints_and_interlock_lamp(qtbot):
    """The Pritel boxes pre-fill with the commissioned bring-up currents
    and keep them across a poll; the interlock latch row has a lamp that
    is green only in the ready state."""
    from keckogeco.gui.mainwindow import MainWindow

    window = MainWindow(FakeClient())
    qtbot.addWidget(window)
    preamp = window.widgets["LFC_PTAMP_PRE_P"]
    pwramp = window.widgets["LFC_PTAMP_I"]
    assert preamp.spin.value() == 600.0  # mA
    assert pwramp.spin.value() == 3.9  # A

    window._on_keywords(
        {
            "LFC_PTAMP_PRE_P": {"value": 0.0},
            "LFC_PTAMP_I": {"value": 0.0},
            "LFC_PTAMP_LATCH": {"value": 1},
        }
    )
    assert (preamp.spin.value(), pwramp.spin.value()) == (600.0, 3.9)
    assert preamp.readback.text() == "0.000 mA"

    latch = window.widgets["LFC_PTAMP_LATCH"]
    assert "#35d07f" in latch.lamp.styleSheet()  # 1 = ready -> green
    for tripped in (0, 3, 5, 4):
        window._on_keywords({"LFC_PTAMP_LATCH": {"value": tripped}})
        assert "#3a4350" in latch.lamp.styleSheet()  # anything else -> grey


def test_pritel_emission_one_click_bringup(qtbot, monkeypatch):
    """Clicking Emission ON runs the full bring-up in the commissioned
    order (FIFO writer queue): preamp setpoint, pump ON, then the Power
    amp box value — re-applying what pump-on's setpoint-zeroing discards
    (Dan, 2026-07-18). OFF touches only the pump keyword. The confirm
    dialog quotes the current the ramp will reach; answering No sends
    nothing."""
    from PyQt6.QtWidgets import QMessageBox

    from keckogeco.gui.mainwindow import MainWindow

    window = MainWindow(FakeClient())
    qtbot.addWidget(window)
    submitted = []
    monkeypatch.setattr(window, "_submit", lambda k, v: submitted.append((k, v)))
    questions = []

    def fake_question(_parent, _title, text, *args, **kwargs):
        questions.append(text)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", staticmethod(fake_question))
    emission = window.widgets["LFC_PTAMP_ONOFF"]
    emission.update_value(False)
    emission.button.click()
    assert submitted == [
        ("LFC_PTAMP_PRE_P", 600.0),
        ("LFC_PTAMP_ONOFF", "1"),
        ("LFC_PTAMP_I", 3.9),
    ]
    assert "turn on the Pritel" in questions[-1]
    assert "ramped to 3.9 amps" in questions[-1]

    # the dialog tracks the box: 1.0 A entered -> 1.0 quoted and written
    submitted.clear()
    window.widgets["LFC_PTAMP_I"].spin.setValue(1.0)
    emission.button.click()
    assert "ramped to 1.0 amps" in questions[-1]
    assert submitted[-1] == ("LFC_PTAMP_I", 1.0)

    # a 0 A box skips the pointless power-amp write
    submitted.clear()
    window.widgets["LFC_PTAMP_I"].spin.setValue(0.0)
    emission.button.click()
    assert submitted == [("LFC_PTAMP_PRE_P", 600.0), ("LFC_PTAMP_ONOFF", "1")]

    submitted.clear()
    emission.update_value(True)
    emission.button.click()
    assert submitted == [("LFC_PTAMP_ONOFF", "0")]
    assert "turn OFF the Pritel" in questions[-1]

    # answering No sends nothing
    submitted.clear()
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.No)
    )
    emission.update_value(False)
    emission.button.click()
    assert submitted == []

    window.poller.stop()
    window.writer.stop()


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
    # arrows step 0.01 per click on all three boxes (Dan, 2026-07-17)
    assert all(w.spin.singleStep() == 0.01 for w in window._wsp_spins.values())
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


def test_flattener_slider_panel(qtbot, monkeypatch):
    """The Spectral Flattener tab builds with the slider absent (the
    FakeClient reports no devices), marks it offline, highlights the
    current slot from a read-back, and routes button clicks through the
    writer thread."""
    from keckogeco.gui.mainwindow import MainWindow

    window = MainWindow(FakeClient())
    qtbot.addWidget(window)
    panel = window._flattener_panel
    assert len(panel.position_buttons) == 6
    assert "not connected" in panel.detail_label.text()  # no nd_slider device

    # a read-back highlights the active slot and clears the offline note
    window._on_call_done("flattener", {"position": 6, "positions": 6})
    assert panel.position_label.text() == "position 6"
    assert panel.detail_label.text() == ""
    assert "bold" in panel.position_buttons[6].styleSheet()
    assert panel.position_buttons[1].styleSheet() == ""

    # unknown position (between slots / not homed) shows the em dash
    window._on_call_done("flattener", {"position": None, "positions": 6})
    assert panel.position_label.text() == "position —"
    assert all(b.styleSheet() == "" for b in panel.position_buttons.values())

    # clicks queue client calls under the "flattener" label
    calls = []
    monkeypatch.setattr(window.writer, "submit_call", lambda label, func: calls.append(label))
    panel.position_buttons[3].click()
    assert calls == ["flattener"]

    # a failed call (server 503: slider offline) restores the offline note
    window._on_write_failed("flattener", "device 'nd_slider' unavailable")
    assert "nd_slider" in panel.detail_label.text()

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
