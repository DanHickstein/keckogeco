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
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

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
        """Mini-comb spectrum from the OSA, or a placeholder until the
        instrument is connected (GPIB is down on the rack)."""
        box = QGroupBox("Mini-comb spectrum (OSA)")
        layout = QVBoxLayout(box)
        try:
            available = self.client.arrays()
        except Exception:  # noqa: BLE001 - older server or offline
            available = []
        pg = None
        if "osa_spectrum" in available:
            try:
                import pyqtgraph as pg
            except ImportError:
                self.statusBar().showMessage("pyqtgraph not installed; spectrum hidden")
        if pg is None:
            placeholder = QLabel("(OSA not connected)")
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            placeholder.setStyleSheet("color: #5a6472; font-style: italic; padding: 30px;")
            layout.addWidget(placeholder)
            return box
        plot = pg.PlotWidget()
        plot.setBackground(PLOT_BG)
        plot.showGrid(x=True, y=True, alpha=0.25)
        curve = plot.plot(pen=pg.mkPen(ACCENT, width=1))
        self._osa_plot = (plot, curve)
        layout.addWidget(plot)
        self.poller.array_names = ["osa_spectrum"]
        self.poller.array_ready.connect(self._on_array)
        return box

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
        if name != "osa_spectrum" or not hasattr(self, "_osa_plot"):
            return
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
