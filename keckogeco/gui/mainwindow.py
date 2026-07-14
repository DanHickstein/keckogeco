"""Engineering GUI main window.

Three tabs of schema-driven panels. **Overview** mirrors the OFF /
STANDBY / FULL COMB + status-lamp layout of the old tkinter GUI
(``KTL server/server_with_gui.py``) that Keck operators already know,
plus the day-to-day controls (EDFAs, Pritel, interlock, RF chain,
temperatures, mini-comb spectrum). **IM Bias Lock** is reserved for the
lock controls (not built yet). **Other** holds the rarely-touched
hardware: EDFA13 (out of the light path), WaveShaper dispersion,
TECs, YJ shutter, VOAs.
"""

from __future__ import annotations

import time
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from . import prefs, spectra
from .client import KeckogecoClient, PollThread, WriteThread
from .theme import ACCENT, PLOT_BG, STATE_COLORS
from .widgets import KeywordDisplay, KeywordSpinBox, OnOffButton, StatusLamp

__all__ = ["MainWindow"]

# a subsystem mix that matches no canonical state is normal during manual
# work — present it as engineering mode, not a fault
_STATE_DISPLAY = {"FAULT": "ENGINEERING MODE"}

# keywords whose writes toggle real optical/RF power -> confirm dialog
_CONFIRM = {
    "LFC_EDFA27_ONOFF",
    "LFC_EDFA13_ONOFF",
    "LFC_EDFA23_ONOFF",
    "LFC_PTAMP_ONOFF",
    "LFC_RFOSCI_ONOFF",
    "LFC_RFAMP_ONOFF",
}


class SelfDestructDialog(QDialog):
    """Requested by Steph Leifer. Fully armed and completely harmless:
    OK and Cancel both just dismiss it."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("keckogeco")
        self._remaining = 5
        layout = QVBoxLayout(self)
        self.label = QLabel(self._message())
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setStyleSheet("font-size: 14px; padding: 12px;")
        layout.addWidget(self.label)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)

    def _message(self) -> str:
        return f"The system will self-destruct in {self._remaining} seconds."

    def _tick(self) -> None:
        self._remaining -= 1
        if self._remaining > 0:
            self.label.setText(self._message())
        else:
            self._timer.stop()
            self.label.setText("💥  ...just kidding. Hi Steph!")


class OsaControls(QWidget):
    """Controls column beside the OSA spectrum plot.

    The default view (factory values below, overridden by the user's
    saved ``[osa_defaults]`` in the GUI prefs file) is pushed to the OSA
    plus continuous sweep when the panel first connects, and again on
    the Default button; "Save as default" persists the current settings.
    Controls re-populate from the read-back after every apply, so they
    always show what the instrument accepted; edits go through the
    writer thread.
    """

    FACTORY_DEFAULTS = {
        "start_nm": 1550.0,
        "stop_nm": 1570.0,
        "resolution_nm": 0.06,  # the 86142B's best
        "sensitivity_dBm": -60.0,
    }
    #: fallback resolution list until the server reports the OSA's own
    RESOLUTIONS_NM = (0.06, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0)

    def __init__(self, submit_settings, submit_sweep, save_spectrum=None, load_spectrum=None):
        super().__init__()
        self._submit_settings = submit_settings  # (**settings) -> queued PUT
        self._submit_sweep = submit_sweep  # (mode) -> queued POST
        self._save_spectrum = save_spectrum  # () -> save-file dialog
        self._load_spectrum = load_spectrum  # ("loaded"|"reference") -> open-file dialog
        saved = prefs.load_section("osa_defaults")
        self.defaults = {
            key: float(saved.get(key, fallback)) for key, fallback in self.FACTORY_DEFAULTS.items()
        }
        self.setMaximumWidth(250)

        form = QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)

        def spin(field: str, spec: dict, default: float) -> KeywordSpinBox:
            widget = KeywordSpinBox(
                field, spec, lambda f, value: self._submit_settings(**{f: value})
            )
            widget.spin.setValue(default)
            return widget

        self.start = spin(
            "start_nm",
            {"units": "nm", "min": 600, "max": 1700, "help": "sweep start wavelength"},
            self.defaults["start_nm"],
        )
        self.stop = spin(
            "stop_nm",
            {"units": "nm", "min": 600, "max": 1700, "help": "sweep stop wavelength"},
            self.defaults["stop_nm"],
        )
        self.sensitivity = spin(
            "sensitivity_dBm",
            # floor at the 86142B's -90 dBm spec: the instrument accepts
            # lower values but only gets slower, never more sensitive
            {"units": "dBm", "min": -90, "max": 30, "help": "measurement sensitivity"},
            self.defaults["sensitivity_dBm"],
        )
        self.resolution = QComboBox()
        self.resolution.setToolTip("resolution bandwidth")
        self._set_resolutions(self.RESOLUTIONS_NM)
        self.resolution.activated.connect(
            lambda index: self._submit_settings(resolution_nm=self.resolution.itemData(index))
        )

        form.addRow("Start", self.start)
        form.addRow("Stop", self.stop)
        form.addRow("Resolution", self.resolution)
        form.addRow("Sensitivity", self.sensitivity)

        sweep = QHBoxLayout()
        self._sweep_buttons: dict[str, QPushButton] = {}
        for text, mode in (("Single", "single"), ("Cont.", "continuous"), ("Stop", "stop")):
            button = QPushButton(text)
            button.setToolTip(f"{mode} sweep")
            button.clicked.connect(lambda _checked, m=mode: self._submit_sweep(m))
            self._sweep_buttons[mode] = button
            sweep.addWidget(button)
        form.addRow("Sweep", sweep)

        config = QHBoxLayout()
        self._default_button = QPushButton("Default")
        self._default_button.clicked.connect(self.apply_defaults)
        save = QPushButton("Save as default")
        save.setToolTip("store the current settings as the default OSA configuration")
        save.clicked.connect(self._save_as_default)
        config.addWidget(self._default_button)
        config.addWidget(save)
        form.addRow("Config", config)
        self._refresh_default_tooltip()

        files = QHBoxLayout()
        for text, tooltip, action in (
            ("Save", "save the live spectrum to a CSV file", lambda: self._save_spectrum()),
            ("Load", "display a saved spectrum", lambda: self._load_spectrum("loaded")),
            (
                "Load ref",
                "display a reference spectrum (remembered across GUI restarts)",
                lambda: self._load_spectrum("reference"),
            ),
        ):
            button = QPushButton(text)
            button.setToolTip(tooltip)
            button.clicked.connect(lambda _checked, a=action: a())
            files.addWidget(button)
        form.addRow("Spectra", files)

    def _refresh_default_tooltip(self) -> None:
        self._default_button.setToolTip(
            f"{self.defaults['start_nm']:g}–{self.defaults['stop_nm']:g} nm, "
            f"{self.defaults['resolution_nm']:g} nm resolution, "
            f"{self.defaults['sensitivity_dBm']:g} dBm sensitivity, continuous sweep"
        )

    def apply_defaults(self) -> None:
        """Push the default mini-comb view to the OSA."""
        self._submit_settings(**self.defaults)
        self._submit_sweep("continuous")

    def current_settings(self) -> dict:
        return {
            "start_nm": self.start.spin.value(),
            "stop_nm": self.stop.spin.value(),
            "resolution_nm": float(self.resolution.currentData()),
            "sensitivity_dBm": self.sensitivity.spin.value(),
        }

    def _save_as_default(self) -> None:
        answer = QMessageBox.question(
            self,
            "Confirm",
            "Are you sure you want to change the default OSA configuration?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.defaults = self.current_settings()
        prefs.save_section("osa_defaults", self.defaults)
        self._refresh_default_tooltip()

    def _set_resolutions(self, values) -> None:
        self.resolution.blockSignals(True)
        self.resolution.clear()
        for value in values:
            self.resolution.addItem(f"{value:g} nm", float(value))
        self.resolution.setCurrentIndex(0)  # best (smallest) first
        self.resolution.blockSignals(False)

    def populate(self, settings: dict) -> None:
        """Update the controls from a GET/PUT read-back."""
        resolutions = settings.get("resolutions_nm")
        if resolutions and self.resolution.count() != len(resolutions):
            self._set_resolutions(resolutions)
        self.start.update_value(settings.get("wl_start_nm"))
        self.stop.update_value(settings.get("wl_stop_nm"))
        self.sensitivity.update_value(settings.get("sensitivity_dBm"))
        res = settings.get("resolution_nm")
        if res is not None and not self.resolution.hasFocus():
            index = min(
                range(self.resolution.count()),
                key=lambda i: abs(self.resolution.itemData(i) - float(res)),
            )
            self.resolution.blockSignals(True)
            self.resolution.setCurrentIndex(index)
            self.resolution.blockSignals(False)
        self.set_sweep(settings.get("sweep_continuous"))

    def set_sweep(self, continuous) -> None:
        """Highlight the sweep button matching the instrument state.

        ``continuous`` False lights Stop (a single sweep also ends there:
        Single is a momentary trigger, not a state); None clears both.
        """
        for mode, button in self._sweep_buttons.items():
            if mode == "single":
                continue
            active = continuous is not None and (mode == "continuous") == bool(continuous)
            button.setStyleSheet(f"color: {ACCENT}; font-weight: bold;" if active else "")


class MainWindow(QMainWindow):
    def __init__(self, client: KeckogecoClient):
        super().__init__()
        self.client = client
        self.setWindowTitle("keckogeco — LFC engineering GUI")
        self.widgets: dict[str, object] = {}  # keyword -> widget

        self.schema = client.schema()

        self.writer = WriteThread(client)
        self.writer.write_failed.connect(self._on_write_failed)
        self.writer.write_ok.connect(self._on_write_ok)
        self.writer.call_done.connect(self._on_call_done)
        self.writer.start()

        self.poller = PollThread(client)
        self.poller.keywords_ready.connect(self._on_keywords)
        self.poller.state_ready.connect(self._on_state)
        self.poller.arrays_available.connect(self._on_arrays_available)
        self.poller.array_ready.connect(self._on_array)
        self.poller.connection_changed.connect(self._on_connection)
        self.poller.start()

        self._build_layout()
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage(f"connecting to {client.base_url} ...")

    # ------------------------------------------------------------- building

    def _submit(self, keyword: str, value) -> None:
        self.statusBar().showMessage(f"writing {keyword} = {value} ...")
        self.writer.submit(keyword, value)

    def _spec(self, keyword: str) -> dict:
        return self.schema.get(keyword, {})

    def _add_spin(self, form: QFormLayout, label: str, keyword: str) -> None:
        if keyword not in self.schema:
            return
        widget = KeywordSpinBox(keyword, self._spec(keyword), self._submit)
        self.widgets[keyword] = widget
        form.addRow(label, widget)

    def _add_display(self, form: QFormLayout, label: str, keyword: str) -> None:
        if keyword not in self.schema:
            return
        widget = KeywordDisplay(keyword, self._spec(keyword))
        self.widgets[keyword] = widget
        form.addRow(label, widget)

    def _add_onoff(self, form: QFormLayout, label: str, keyword: str) -> None:
        if keyword not in self.schema:
            return
        widget = OnOffButton(
            keyword, self._spec(keyword), self._submit, confirm=keyword in _CONFIRM, label=label
        )
        self.widgets[keyword] = widget
        form.addRow(label, widget)

    def _build_layout(self) -> None:
        tabs = QTabWidget()
        tabs.addTab(self._overview_tab(), "Overview")
        tabs.addTab(self._im_lock_tab(), "IM Bias Lock")
        tabs.addTab(self._other_tab(), "Other")
        self.setCentralWidget(tabs)

    def _overview_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.addWidget(self._comb_state_panel())

        row2 = QHBoxLayout()
        row2.addWidget(self._edfa_panel("EDFA 27 dBm", "LFC_EDFA27"))
        row2.addWidget(self._edfa_panel("EDFA 23 dBm", "LFC_EDFA23"))
        row2.addWidget(self._interlock_panel())
        row2.addWidget(self._pritel_panel())
        outer.addLayout(row2)

        row3 = QHBoxLayout()
        row3.addWidget(self._rf_panel())
        row3.addWidget(self._temperature_panel(), stretch=1)
        outer.addLayout(row3)

        outer.addWidget(self._osa_panel(), stretch=1)
        return page

    def _im_lock_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        placeholder = QLabel("IM bias lock controls will live here — not built yet.")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder.setStyleSheet("color: #5a6472; font-style: italic;")
        layout.addWidget(placeholder)
        return page

    def _other_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)

        row1 = QHBoxLayout()
        row1.addWidget(self._edfa_panel("EDFA 13 dBm (not in use)", "LFC_EDFA13"))
        row1.addWidget(self._waveshaper_panel())
        row1.addWidget(self._tec_panel())
        outer.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(self._shutter_panel())
        row2.addWidget(self._voa_panel(), stretch=1)
        outer.addLayout(row2)

        outer.addStretch(1)
        return page

    def _comb_state_panel(self) -> QGroupBox:
        box = QGroupBox("Comb State")
        outer = QHBoxLayout(box)

        self.state_banner = QLabel("UNKNOWN")
        self.state_banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.state_banner.setMinimumWidth(140)
        self._set_banner("UNKNOWN")
        outer.addWidget(self.state_banner)

        # lamp order per operations: RF chain first, then amplification
        self.subsystem_lamps: dict[str, StatusLamp] = {}
        lamps = QGridLayout()
        lamps.setHorizontalSpacing(10)
        for column, (key, label) in enumerate(
            [
                ("rf_oscillator", "RF Osc"),
                ("im_lock", "IM Lock"),  # from LFC_IM_LOCK_MODE, not /state
                ("rf_amplifier", "RF Amp"),
                ("edfa27", "EDFA27"),
                ("edfa23", "EDFA23"),
                ("ptamp", "Pritel"),
            ]
        ):
            lamp = StatusLamp(label)
            self.subsystem_lamps[key] = lamp
            lamps.addWidget(lamp, 0, column, alignment=Qt.AlignmentFlag.AlignCenter)
            lamps.addWidget(QLabel(label), 1, column, alignment=Qt.AlignmentFlag.AlignCenter)
        outer.addLayout(lamps)

        self.action_label = QLabel("")
        self.action_label.setWordWrap(True)
        outer.addWidget(self.action_label, stretch=1)

        buttons = QHBoxLayout()
        for text, action in (
            ("STANDBY", "set_standby"),
            ("FULL COMB", "set_full_comb"),
            ("OFF", "set_off"),
        ):
            button = QPushButton(text)
            button.setToolTip(f"Run the {text} transition sequence")
            button.clicked.connect(lambda _checked, a=action, t=text: self._start_action(a, t))
            buttons.addWidget(button)
        abort = QPushButton("Abort")
        abort.setToolTip("Abort the running transition")
        abort.clicked.connect(self._abort_action)
        buttons.addWidget(abort)
        boom = QPushButton("Self-destruct")
        boom.setToolTip("for Steph")
        boom.setStyleSheet("color: #e05252;")
        boom.clicked.connect(lambda: SelfDestructDialog(self).exec())
        buttons.addWidget(boom)
        outer.addLayout(buttons)
        return box

    def _set_banner(self, state_name: str) -> None:
        color = STATE_COLORS.get(state_name, STATE_COLORS["UNKNOWN"])
        self.state_banner.setText(_STATE_DISPLAY.get(state_name, state_name))
        self.state_banner.setStyleSheet(
            "font-size: 14px; font-weight: bold; color: white; "
            f"background-color: {color}; padding: 5px 10px; border-radius: 4px;"
        )

    def _start_action(self, action: str, label: str) -> None:
        answer = QMessageBox.question(
            self,
            "Confirm",
            f"Really go to {label}? This runs a multi-step power sequence.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.client.start_action(action)
            self.statusBar().showMessage(f"started {action}")
        except Exception as exc:  # noqa: BLE001 - show any refusal (e.g. busy)
            self.statusBar().showMessage(f"ACTION REFUSED: {exc}", 10000)

    def _abort_action(self) -> None:
        try:
            self.client.abort_action()
            self.statusBar().showMessage("abort requested")
        except Exception as exc:  # noqa: BLE001
            self.statusBar().showMessage(f"abort failed: {exc}", 10000)

    def _edfa_panel(self, title: str, prefix: str) -> QGroupBox:
        box = QGroupBox(title)
        form = QFormLayout(box)
        self._add_onoff(form, "Emission", f"{prefix}_ONOFF")
        self._add_spin(form, "Setpoint", f"{prefix}_P")
        self._add_display(form, "Input power", f"{prefix}_INPUT_POWER_MONITOR")
        return box

    def _interlock_panel(self) -> QGroupBox:
        box = QGroupBox("Interlock")
        form = QFormLayout(box)
        self._add_display(form, "Pritel latch", "LFC_PTAMP_LATCH")
        reset = QPushButton("Reset latch")
        reset.clicked.connect(lambda: self._submit("LFC_PTAMP_LATCH", "1"))
        form.addRow("", reset)
        return box

    def _pritel_panel(self) -> QGroupBox:
        box = QGroupBox("Pritel amplifier")
        form = QFormLayout(box)
        self._add_onoff(form, "Pump", "LFC_PTAMP_ONOFF")
        self._add_spin(form, "Preamp", "LFC_PTAMP_PRE_P")
        self._add_spin(form, "Power amp", "LFC_PTAMP_I")
        self._add_display(form, "Output", "LFC_PTAMP_OUT")
        return box

    def _rf_panel(self) -> QGroupBox:
        box = QGroupBox("RF chain")
        form = QFormLayout(box)
        self._add_onoff(form, "Oscillator PSU", "LFC_RFOSCI_ONOFF")
        self._add_display(form, "Osc current", "LFC_RFOSCI_I")
        self._add_display(form, "Osc voltage", "LFC_RFOSCI_V")
        self._add_onoff(form, "Amplifier PSU", "LFC_RFAMP_ONOFF")
        self._add_display(form, "Amp current", "LFC_RFAMP_I")
        self._add_display(form, "Amp voltage", "LFC_RFAMP_V")
        return box

    def _temperature_panel(self) -> QGroupBox:
        box = QGroupBox("Temperatures")
        grid = QGridLayout(box)
        grid.setHorizontalSpacing(18)
        present = [
            (keyword, label)
            for keyword, label in [
                ("LFC_T_RACK_TOP", "Rack top"),
                ("LFC_T_RACK_MID", "Rack mid"),
                ("LFC_T_RACK_BOT", "Rack bottom"),
                ("LFC_T_GLY_RACK_IN", "Glycol in"),
                ("LFC_T_GLY_RACK_OUT", "Glycol out"),
                ("LFC_T_EOCB_IN", "EOCB in"),
                ("LFC_T_EOCB_OUT", "EOCB out"),
            ]
            if keyword in self.schema
        ]
        for index, (keyword, label) in enumerate(present):
            row, pair = divmod(index, 2)
            grid.addWidget(QLabel(label), row, pair * 2)
            display = KeywordDisplay(keyword, self._spec(keyword))
            self.widgets[keyword] = display
            grid.addWidget(display, row, pair * 2 + 1)
        return box

    def _osa_panel(self) -> QGroupBox:
        """Mini-comb spectrum from the OSA. Starts as a placeholder; the
        plot + controls are wired in by _on_arrays_available the moment the
        server offers the osa_spectrum array (e.g. after the OSA comes
        online)."""
        box = QGroupBox("Mini-comb spectrum (OSA)")
        self._osa_layout = QHBoxLayout(box)
        self._osa_plot = None
        self._osa_controls: OsaControls | None = None
        self._osa_placeholder = QLabel("(OSA not connected)")
        self._osa_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._osa_placeholder.setStyleSheet("color: #5a6472; font-style: italic; padding: 30px;")
        self._osa_layout.addWidget(self._osa_placeholder, stretch=1)
        return box

    def _on_arrays_available(self, names: list) -> None:
        if self._osa_plot is not None or "osa_spectrum" not in names:
            return
        try:
            import pyqtgraph as pg
        except ImportError:
            self._osa_placeholder.setText("(pyqtgraph not installed; spectrum hidden)")
            return
        plot = pg.PlotWidget()
        plot.setBackground(PLOT_BG)
        plot.showGrid(x=True, y=True, alpha=0.25)
        plot.addLegend(offset=(-10, 10), labelTextColor="#8b96a5")
        curve = plot.plot(pen=pg.mkPen(ACCENT, width=1), name="live")
        self._osa_plot = (plot, curve)
        self._osa_curves: dict[str, object] = {}  # "loaded"/"reference" overlays
        self._osa_placeholder.deleteLater()
        self._osa_layout.addWidget(plot, stretch=1)
        self._osa_controls = OsaControls(
            self._osa_apply, self._osa_sweep, self._osa_save, self._osa_load
        )
        self._osa_layout.addWidget(self._osa_controls)
        self.poller.array_names = ["osa_spectrum"]
        # per Dan: connecting means "show me the standard mini-comb view",
        # so the defaults are applied to the OSA, not just displayed
        self._osa_controls.apply_defaults()
        self._restore_reference()

    #: overlay pens (live is the accent color); reference is dashed.
    #: z stacks the overlays behind the live trace (0): loaded behind
    #: live, reference behind both.
    _CURVE_STYLE = {"loaded": ("#e8a33d", None, -1), "reference": ("#b085f5", "dash", -2)}

    def _osa_set_curve(self, kind: str, x: list, y: list) -> None:
        curve = self._osa_curves.get(kind)
        if curve is None:
            import pyqtgraph as pg

            color, dash, z = self._CURVE_STYLE[kind]
            pen = pg.mkPen(
                color, width=1, style=Qt.PenStyle.DashLine if dash else Qt.PenStyle.SolidLine
            )
            plot, _live = self._osa_plot
            curve = plot.plot(pen=pen, name=kind)
            curve.setZValue(z)
            self._osa_curves[kind] = curve
        curve.setData(x, y)

    def _spectra_dir(self) -> Path:
        directory = prefs.GUI_CONFIG_PATH.parent.parent / "spectra"
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def _osa_save(self) -> None:
        data = getattr(self, "_osa_data", None)
        if not data or not data.get("x"):
            self.statusBar().showMessage("no live spectrum to save yet", 5000)
            return
        default = self._spectra_dir() / time.strftime("osa_%Y-%m-%d_%H%M%S.csv")
        path, _filter = QFileDialog.getSaveFileName(
            self, "Save spectrum", str(default), "CSV files (*.csv)"
        )
        if not path:
            return
        metadata = dict(self._osa_controls.current_settings())
        metadata["x_label"] = data.get("x_label", "")
        metadata["y_label"] = data.get("y_label", "")
        metadata["points"] = len(data["x"])
        try:
            spectra.save_spectrum_csv(path, data["x"], data["y"], metadata)
        except OSError as exc:
            self.statusBar().showMessage(f"SAVE FAILED: {exc}", 10000)
            return
        self.statusBar().showMessage(f"saved {path}", 8000)

    def _osa_load(self, kind: str) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, f"Load {kind} spectrum", str(self._spectra_dir()), "CSV files (*.csv)"
        )
        if not path:
            return
        try:
            x, y, _metadata = spectra.load_spectrum_csv(path)
        except (OSError, ValueError) as exc:
            self.statusBar().showMessage(f"LOAD FAILED: {exc}", 10000)
            return
        self._osa_set_curve(kind, x, y)
        if kind == "reference":
            prefs.save_section("osa_reference", {"csv": Path(path).as_posix()})
        self.statusBar().showMessage(f"{kind} spectrum: {path}", 8000)

    def _restore_reference(self) -> None:
        """Re-display the saved reference spectrum, if one was ever loaded."""
        saved = prefs.load_section("osa_reference").get("csv")
        if not saved:
            return
        try:
            x, y, _metadata = spectra.load_spectrum_csv(saved)
        except (OSError, ValueError) as exc:
            self.statusBar().showMessage(f"reference spectrum not restored: {exc}", 10000)
            return
        self._osa_set_curve("reference", x, y)

    def _osa_apply(self, **settings) -> None:
        if settings:
            self.writer.submit_call("OSA settings", lambda c: c.osa_apply(**settings))
        else:
            self.writer.submit_call("OSA settings", lambda c: c.osa_settings())

    def _osa_sweep(self, mode: str) -> None:
        self.writer.submit_call("OSA sweep", lambda c: c.osa_sweep(mode))

    def _on_call_done(self, label: str, result) -> None:
        if self._osa_controls is None or not isinstance(result, dict):
            return
        if label == "OSA settings":
            self._osa_controls.populate(result)
        elif label == "OSA sweep":
            self._osa_controls.set_sweep(result.get("sweep_continuous"))

    def _waveshaper_panel(self) -> QGroupBox:
        # the whole interaction is two numbers; the spin boxes track the
        # value currently applied (server reads back its softstore)
        box = QGroupBox("WaveShaper dispersion")
        form = QFormLayout(box)
        self._add_spin(form, "GDD", "LFC_WSP_PHASE")
        self._add_spin(form, "TOD", "LFC_WSP_TOD")
        return box

    def _tec_panel(self) -> QGroupBox:
        box = QGroupBox("TECs + IM bias")
        form = QFormLayout(box)
        self._add_spin(form, "IM bias", "LFC_IM_BIAS")
        self._add_spin(form, "PPLN temp", "LFC_PPLN_T")
        self._add_spin(form, "Waveguide temp", "LFC_WGD_T")
        return box

    def _shutter_panel(self) -> QGroupBox:
        box = QGroupBox("Shutters")
        form = QFormLayout(box)
        self._add_onoff(form, "YJ shutter", "LFC_YJ_SHUTTER")
        return box

    def _voa_panel(self) -> QGroupBox:
        # Serial <-> keyword mapping mirrors config/keckogeco.toml. The VOAs
        # are not in the optical chain, so they are listed by serial number;
        # relabel by wavelength if they ever get installed on those fibers.
        box = QGroupBox("VOA attenuation (not in optical chain)")
        layout = QHBoxLayout(box)
        for serial, keyword in [
            ("NO-303699-01", "LFC_VOA1550_ATTEN"),
            ("NO-303700-01", "LFC_VOA1310_ATTEN"),
            ("NO-311029-01", "LFC_VOA2000_ATTEN"),
        ]:
            if keyword not in self.schema:
                continue
            form = QFormLayout()
            self._add_spin(form, serial, keyword)
            layout.addLayout(form)
        return box

    # --------------------------------------------------------------- slots

    def _on_keywords(self, snapshot: dict) -> None:
        for keyword, payload in snapshot.items():
            widget = self.widgets.get(keyword)
            if widget is not None and hasattr(widget, "update_value"):
                widget.update_value(payload["value"])
        # the IM lock lamp is keyword-driven (servo PID mode), not in /state
        im_lock = snapshot.get("LFC_IM_LOCK_MODE")
        if im_lock is not None:
            value = im_lock.get("value")
            self.subsystem_lamps["im_lock"].set_state(None if value is None else bool(value))

    def _on_state(self, state: dict) -> None:
        self._set_banner(state.get("state", "UNKNOWN"))
        for key, lamp in self.subsystem_lamps.items():
            if key == "im_lock":
                continue  # driven from the keyword snapshot instead
            lamp.set_state(state.get("subsystems", {}).get(key))
        action = state.get("action")
        if action and action.get("running"):
            self.action_label.setText(
                f"⏳ {action['name']} — step {action['step']}"
                + (f"/{action['total_steps']}" if action.get("total_steps") else "")
                + f": {action['message']}"
            )
        elif action and action.get("error"):
            self.action_label.setText(f"❌ {action['name']}: {action['error']}")
        elif action:
            self.action_label.setText(f"✓ last action {action['name']}: {action['message']}")
        else:
            self.action_label.setText("")

    def _on_array(self, name: str, data: dict) -> None:
        if name != "osa_spectrum" or self._osa_plot is None:
            return
        self._osa_data = data  # kept for "Save"
        plot, curve = self._osa_plot
        curve.setData(data.get("x", []), data.get("y", []))
        plot.setLabel("bottom", data.get("x_label", ""))
        plot.setLabel("left", data.get("y_label", ""))

    def _on_connection(self, ok: bool, detail: str) -> None:
        if ok:
            self.statusBar().showMessage(f"connected to {self.client.base_url}")
        else:
            self.statusBar().showMessage(f"NOT CONNECTED: {detail}")

    def _on_write_ok(self, keyword: str, value) -> None:
        self.statusBar().showMessage(f"{keyword} = {value}", 5000)

    def _on_write_failed(self, keyword: str, error: str) -> None:
        self.statusBar().showMessage(f"WRITE FAILED {keyword}: {error}", 10000)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self.poller.stop()
        self.writer.stop()
        super().closeEvent(event)
