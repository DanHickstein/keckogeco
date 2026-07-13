"""Engineering GUI main window.

Hand-built panels assembled from schema-driven widgets. The Comb State
panel mirrors the OFF / STANDBY / FULL COMB + status-lamp layout of the
old tkinter GUI (``KTL server/server_with_gui.py``) that Keck operators
already know; the rest is the full-control surface for Octave.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from .client import KeckogecoClient, PollThread, WriteThread
from .widgets import KeywordDisplay, KeywordSpinBox, OnOffButton, StatusLamp

__all__ = ["MainWindow"]

_STATE_COLORS = {
    "FULL COMB": "#2e7d32",
    "STANDBY": "#f9a825",
    "OFF": "#616161",
    "FAULT": "#c62828",
    "UNKNOWN": "#9e9e9e",
    "TRANSITIONING": "#1565c0",
}

# keywords whose writes toggle real optical/RF power -> confirm dialog
_CONFIRM = {
    "LFC_EDFA27_ONOFF",
    "LFC_EDFA13_ONOFF",
    "LFC_EDFA23_ONOFF",
    "LFC_PTAMP_ONOFF",
    "LFC_RFOSCI_ONOFF",
    "LFC_RFAMP_ONOFF",
}


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
        self.writer.start()

        self.poller = PollThread(client)
        self.poller.keywords_ready.connect(self._on_keywords)
        self.poller.state_ready.connect(self._on_state)
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
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.addWidget(self._comb_state_panel())

        grid = QGridLayout()
        grid.addWidget(self._edfa_panel("EDFA 27 dBm", "LFC_EDFA27"), 0, 0)
        grid.addWidget(self._edfa_panel("EDFA 13 dBm", "LFC_EDFA13"), 0, 1)
        grid.addWidget(self._edfa_panel("EDFA 23 dBm", "LFC_EDFA23"), 0, 2)
        grid.addWidget(self._pritel_panel(), 1, 0)
        grid.addWidget(self._rf_panel(), 1, 1)
        grid.addWidget(self._seed_tec_panel(), 1, 2)
        grid.addWidget(self._temperature_panel(), 2, 0, 1, 3)
        grid.addWidget(self._voa_panel(), 3, 0, 1, 3)
        outer.addLayout(grid)

        spectra = self._spectra_panel()
        if spectra is not None:
            outer.addWidget(spectra, stretch=1)

        self.setCentralWidget(central)

    def _spectra_panel(self) -> QGroupBox | None:
        """OSA spectrum + WaveShaper profile plots (pyqtgraph), if the
        server exposes those arrays."""
        try:
            available = self.client.arrays()
        except Exception:  # noqa: BLE001 - older server or offline
            available = []
        wanted = [name for name in ("osa_spectrum", "wsp_profile") if name in available]
        if not wanted:
            return None
        try:
            import pyqtgraph as pg
        except ImportError:
            self.statusBar().showMessage("pyqtgraph not installed; spectra hidden")
            return None

        box = QGroupBox("Spectra")
        layout = QHBoxLayout(box)
        self._plots: dict[str, object] = {}
        titles = {"osa_spectrum": "OSA", "wsp_profile": "WaveShaper profile"}
        for name in wanted:
            plot = pg.PlotWidget(title=titles[name])
            plot.showGrid(x=True, y=True, alpha=0.3)
            curve = plot.plot(pen=pg.mkPen("#1565c0", width=1))
            self._plots[name] = (plot, curve)
            layout.addWidget(plot)
        self.poller.array_names = wanted
        self.poller.array_ready.connect(self._on_array)
        return box

    def _on_array(self, name: str, data: dict) -> None:
        entry = getattr(self, "_plots", {}).get(name)
        if entry is None:
            return
        plot, curve = entry
        curve.setData(data.get("x", []), data.get("y", []))
        plot.setLabel("bottom", data.get("x_label", ""))
        plot.setLabel("left", data.get("y_label", ""))

    def _comb_state_panel(self) -> QGroupBox:
        box = QGroupBox("Comb State")
        layout = QHBoxLayout(box)

        self.state_banner = QLabel("UNKNOWN")
        self.state_banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.state_banner.setMinimumWidth(180)
        self.state_banner.setStyleSheet(
            "font-size: 20px; font-weight: bold; color: white; "
            "background-color: #9e9e9e; padding: 8px; border-radius: 4px;"
        )
        layout.addWidget(self.state_banner)

        self.subsystem_lamps: dict[str, StatusLamp] = {}
        lamps = QGridLayout()
        for column, (key, label) in enumerate(
            [
                ("rf_oscillator", "RF osc"),
                ("rf_amplifier", "RF amp"),
                ("edfa23", "EDFA23"),
                ("edfa27", "EDFA27"),
                ("ptamp", "Pritel"),
                ("rep_rate", "Rep rate"),
            ]
        ):
            lamp = StatusLamp(label)
            self.subsystem_lamps[key] = lamp
            lamps.addWidget(lamp, 0, column, alignment=Qt.AlignmentFlag.AlignCenter)
            lamps.addWidget(QLabel(label), 1, column, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addLayout(lamps)

        self.action_label = QLabel("")
        self.action_label.setWordWrap(True)
        layout.addWidget(self.action_label, stretch=1)

        buttons = QVBoxLayout()
        for text, action in (
            ("Go to STANDBY", "set_standby"),
            ("Go to FULL COMB", "set_full_comb"),
            ("Turn OFF", "set_off"),
        ):
            button = QPushButton(text)
            button.clicked.connect(lambda _checked, a=action, t=text: self._start_action(a, t))
            buttons.addWidget(button)
        abort = QPushButton("Abort action")
        abort.clicked.connect(self._abort_action)
        buttons.addWidget(abort)
        layout.addLayout(buttons)
        return box

    def _start_action(self, action: str, label: str) -> None:
        answer = QMessageBox.question(
            self,
            "Confirm",
            f"Really {label}? This runs a multi-step power sequence.",
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

    def _pritel_panel(self) -> QGroupBox:
        box = QGroupBox("Pritel amplifier")
        form = QFormLayout(box)
        self._add_onoff(form, "Pump", "LFC_PTAMP_ONOFF")
        self._add_spin(form, "Preamp", "LFC_PTAMP_PRE_P")
        self._add_spin(form, "Power amp", "LFC_PTAMP_I")
        self._add_display(form, "Output", "LFC_PTAMP_OUT")
        self._add_display(form, "Interlock", "LFC_PTAMP_LATCH")
        reset = QPushButton("Reset interlock latch")
        reset.clicked.connect(lambda: self._submit("LFC_PTAMP_LATCH", "1"))
        form.addRow("", reset)
        self._add_onoff(form, "YJ shutter", "LFC_YJ_SHUTTER")
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

    def _seed_tec_panel(self) -> QGroupBox:
        box = QGroupBox("Seed laser + TECs")
        form = QFormLayout(box)
        self._add_spin(form, "RIO temp", "LFC_RIO_T")
        self._add_spin(form, "RIO current", "LFC_RIO_I")
        self._add_spin(form, "IM bias", "LFC_IM_BIAS")
        self._add_spin(form, "PPLN temp", "LFC_PPLN_T")
        self._add_spin(form, "Waveguide temp", "LFC_WGD_T")
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

    def _temperature_panel(self) -> QGroupBox:
        box = QGroupBox("Rack temperatures")
        layout = QHBoxLayout(box)
        for keyword, label in [
            ("LFC_T_RACK_TOP", "Rack top"),
            ("LFC_T_RACK_MID", "Rack mid"),
            ("LFC_T_RACK_BOT", "Rack bottom"),
            ("LFC_T_GLY_RACK_IN", "Glycol in"),
            ("LFC_T_GLY_RACK_OUT", "Glycol out"),
            ("LFC_T_EOCB_IN", "EOCB in"),
            ("LFC_T_EOCB_OUT", "EOCB out"),
        ]:
            if keyword not in self.schema:
                continue
            column = QVBoxLayout()
            column.addWidget(QLabel(label), alignment=Qt.AlignmentFlag.AlignCenter)
            display = KeywordDisplay(keyword, self._spec(keyword))
            self.widgets[keyword] = display
            column.addWidget(display, alignment=Qt.AlignmentFlag.AlignCenter)
            layout.addLayout(column)
        return box

    # --------------------------------------------------------------- slots

    def _on_keywords(self, snapshot: dict) -> None:
        for keyword, payload in snapshot.items():
            widget = self.widgets.get(keyword)
            if widget is not None and hasattr(widget, "update_value"):
                widget.update_value(payload["value"])

    def _on_state(self, state: dict) -> None:
        name = state.get("state", "UNKNOWN")
        color = _STATE_COLORS.get(name, "#9e9e9e")
        self.state_banner.setText(name)
        self.state_banner.setStyleSheet(
            "font-size: 20px; font-weight: bold; color: white; "
            f"background-color: {color}; padding: 8px; border-radius: 4px;"
        )
        for key, lamp in self.subsystem_lamps.items():
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
