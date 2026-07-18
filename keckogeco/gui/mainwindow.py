"""Engineering GUI main window.

Three tabs of schema-driven panels. **Overview** mirrors the OFF /
STANDBY / FULL COMB + status-lamp layout of the old tkinter GUI
(``KTL server/server_with_gui.py``) that Keck operators already know,
plus the day-to-day controls (EDFAs, Pritel, interlock, RF chain,
temperatures, mini-comb spectrum). **IM Bias Lock** is the servo
workbench: lock controls (bias, RF attenuation, setpoint, lock/unlock)
with photodiode + bias strip charts on top, the bias-scan panel in the
middle (transfer function; only while unlocked), and a mirror of the
OSA spectrum at the bottom so the comb is visible while adjusting.
**Spectral Flattener** holds the flattener hardware reachable from this
laptop — currently just the ND-filter output slider (the SLM flattener
itself stays on the Menlo laptop, see docs/user_guide/menlo_flattener.md).
**Other** holds the rarely-touched hardware: EDFA13 (out of the light
path), WaveShaper dispersion, TECs, YJ shutter, VOAs.
"""

from __future__ import annotations

import platform
import time
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
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

from ..comb.locking import recommend_lock_point
from . import prefs, spectra
from .client import KeckogecoClient, PollThread, WriteThread
from .laptop import TEMP_HOT_C, TEMP_WARN_C, LaptopPollThread, temp_state
from .theme import ACCENT, MUTED, PLOT_BG, STATE_COLORS
from .widgets import (
    KeywordDisplay,
    KeywordSpinBox,
    LampDisplay,
    OnOffButton,
    PrecisionDisplay,
    SelectAllSpinBox,
    StatusLamp,
    ThermoArray,
)

__all__ = ["MainWindow"]


def _hint_label(text: str, tooltip: str = "") -> QLabel:
    """Gray hint shown on its own row under the control it describes
    (recommended values, normal operating ranges)."""
    label = QLabel(text)
    label.setStyleSheet(f"color: {MUTED};")
    if tooltip:
        label.setToolTip(tooltip)
    return label


# a subsystem mix that matches no canonical state is normal during manual
# work — present it as engineering mode, not a fault
_STATE_DISPLAY = {"FAULT": "ENGINEERING MODE"}

# Thermocouple channel labels mirror drivers/usb2408.DEFAULT_POSITIONS
# (documented at commissioning, Jun 2023); full position text goes in the
# tooltip. The rack board's ch7 is permanently unconnected and left out.
# Values arrive as the LFC_TEMP_TEST1/2 array keywords, so every channel
# is shown — not just the seven with individual LFC_T_* keywords.
# Each channel's last value is its normal-operation baseline, recorded on
# the live rack 2026-07-14 (system in its normal state; five /keywords
# snapshots averaged). Readings more than ±_TEMP_TOLERANCE_C from the
# baseline are colored bold red (hot) / bold blue (cold) — a per-channel
# band, because "normal" spans 14 °C glycol to a 48 °C RF amplifier.
_THERMO_PANELS = (
    (
        "LFC_TEMP_TEST1",
        "Rack",
        [
            (0, "Side baffle", "Rack side baffle (middle side rack)", 28.7),
            (1, "WaveShaper", "Waveshaper (upper rack)", 26.4),
            (2, "Rb clock", "Rb clock (middle rack)", 27.0),
            (3, "Pritel", "Pritel (middle upper rack)", 26.0),
            (4, "Glycol out", "Rack glycol out", 19.6),
            (5, "Glycol in", "Rack glycol in", 14.1),
            (6, "PSU shelf", "Power supply shelf (bottom rack)", 26.0),
        ],
    ),
    (
        "LFC_TEMP_TEST2",
        "Optical table (EOCB)",
        [
            (0, "RF oscillator", "RF oscillator", 40.3),
            (1, "RF amplifier", "RF amplifier", 48.0),
            (2, "Phase mods", "Main phase modulators", 32.7),
            (3, "Filter cavity", "Filter cavity", 28.0),
            (4, "Glycol out", "Board glycol out", 15.6),
            (5, "Glycol in", "Board glycol in", 34.9),
            (6, "Compression", "Compression stage", 23.1),
            (7, "Rb cell", "Rubidium (Rb) cell D2-210", 24.2),
        ],
    ),
)

#: deviation from a channel's baseline that turns its readout red/blue
_TEMP_TOLERANCE_C = 3.0

#: readout styles for the laptop's absolute temperature bands
#: (temp_state in gui/laptop.py; "ok" stays plain)
_LAPTOP_TEMP_STYLES = {
    "hot": "color: #e05252; font-weight: bold;",
    "warn": "color: #c78a00; font-weight: bold;",
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
        for text, tooltip, mode in (
            ("Single", "trigger one sweep, then hold it on screen", "single"),
            ("Cont.", "sweep continuously (the live view)", "continuous"),
            ("Stop", "", "stop"),
        ):
            button = QPushButton(text)
            if tooltip:
                button.setToolTip(tooltip)
            button.clicked.connect(lambda _checked, m=mode: self._submit_sweep(m))
            self._sweep_buttons[mode] = button
            sweep.addWidget(button)
        form.addRow("Sweep", sweep)

        config = QHBoxLayout()
        self._default_button = QPushButton("Default")
        self._default_button.clicked.connect(self.apply_defaults)
        save = QPushButton("Save as default")
        save.setToolTip(
            "remember the current settings as the default view "
            "(applied every time the GUI connects)"
        )
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


class ImServoPanel(QWidget):
    """Lock controls for the IM bias servo (top of the IM Bias Lock tab).

    Locking is deliberately manual (Dan, 2026-07-15): the operator sets
    the photodiode lockpoint, the PI gains, and the Bias start here,
    then presses Lock — the GUI writes Bias start to LFC_IM_BIAS and
    engages LFC_IM_LOCK_MODE (the server copies that bias into the
    SIM960 output offset on engage, so the PID takes over bumplessly).
    While locked the Bias out box is read-only and follows the PID's
    live output; the lockpoint stays adjustable. The scan panel below
    suggests values for these boxes. Live readouts come from the
    im_scan array payload; gains populate from GET/PUT /im read-backs.
    The strip charts beside this column are owned by the main window.
    """

    def __init__(
        self,
        set_lock,
        bias_widget=None,
        rf_att_widget=None,
        setpoint_widget=None,
        prop_widget=None,
        intg_widget=None,
        bias_start_widget=None,
    ):
        super().__init__()
        self._set_lock = set_lock
        self._locked: bool | None = None
        self.setMaximumWidth(500)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)

        # the one-button workflow comes first: the lamp in the corner,
        # Locked/Unlocked, and the Lock/Unlock toggle (no row label)
        status_row = QHBoxLayout()
        self.lamp = StatusLamp("IM lock")
        self.status = QLabel("—")
        self.status.setToolTip("SIM960 output mode (manual bias vs engaged PID)")
        self.lock_button = QPushButton("Lock")
        self.lock_button.clicked.connect(self._toggle_lock)
        status_row.addWidget(self.lamp)
        status_row.addWidget(self.status)
        status_row.addWidget(self.lock_button, stretch=1)
        grid.addLayout(status_row, 0, 0, 1, 4)

        self.bias = bias_widget
        self.input_v = QLabel("—")
        self.input_v.setToolTip("servo measure input — the minicomb photodiode voltage")
        self.setpoint = setpoint_widget
        self.prop = prop_widget
        self.intg = intg_widget
        self.bias_start = bias_start_widget
        # two columns, filled top to bottom, keep the panel short (the
        # spectrum at the bottom of the tab needs the vertical space)
        pairs = [
            (label, widget)
            for label, widget in (
                ("Bias out", bias_widget),
                ("Photodiode", self.input_v),
                ("Lockpoint", setpoint_widget),
                ("P gain", prop_widget),
                ("I gain", intg_widget),
                ("Bias start", bias_start_widget),
                # LFC_IM_RF_ATT: the VCA drive on RF oscillator supply ch 3.
                # Iterate it against the bias while flattening the mini-comb.
                ("RF atten", rf_att_widget),
            )
            if widget is not None
        ]
        rows = (len(pairs) + 1) // 2
        for index, (label, widget) in enumerate(pairs):
            column, row = divmod(index, rows)
            grid.addWidget(QLabel(label), row + 1, column * 2)
            grid.addWidget(widget, row + 1, column * 2 + 1)
        if rf_att_widget is not None:
            # the RF-atten hint takes the slot after the last pair
            column, row = divmod(len(pairs), rows)
            grid.addWidget(
                _hint_label(
                    "recommended 0.80–0.85 V",
                    "normal operating range of the RF attenuator drive "
                    "(docs: locking procedure step 4)",
                ),
                row + 1,
                column * 2,
                1,
                2,
            )
        self._show_locked(None)

    def _toggle_lock(self) -> None:
        # unknown state (server unreachable, scanning): treat as unlocked
        self._set_lock(not self._locked)

    def _show_locked(self, locked: bool | None) -> None:
        """Flip the status row + bias-box editability to the lock state
        (None = unknown/scanning: leave the bias box alone)."""
        self._locked = locked
        self.lamp.set_state(locked)
        if locked is None:
            return
        self.status.setText("Locked" if locked else "Unlocked")
        self.lock_button.setText("Unlock" if locked else "Lock")
        self.lock_button.setToolTip(
            "back to manual bias output (LFC_IM_LOCK_MODE = 0)"
            if locked
            else "engage the PID at the setpoint, starting from the Bias start "
            "value (writes LFC_IM_BIAS, then LFC_IM_LOCK_MODE = 1)"
        )
        if self.bias is not None:
            self.bias.setEnabled(not locked)

    def update_status(self, payload: dict) -> None:
        """Refresh mode + readouts from the im_scan array (or a PUT /im
        read-back — same keys)."""
        if payload.get("running"):
            self.status.setText("scanning …")
            self.lamp.set_state(None)
        elif payload.get("mode") is not None:
            self._show_locked(payload["mode"] == "PID")
        if self.setpoint is not None and payload.get("setpoint_V") is not None:
            self.setpoint.update_value(payload["setpoint_V"])
        if self.prop is not None and payload.get("prop_gain") is not None:
            self.prop.update_value(payload["prop_gain"])
        if self.intg is not None and payload.get("intg_gain") is not None:
            self.intg.update_value(payload["intg_gain"])
        # while scanning the live input is the last recorded sweep point
        value = payload["y"][-1] if payload.get("running") and payload.get("y") else None
        if value is None:
            value = payload.get("input_V")
        if value is not None:
            self.input_v.setText(f"{value:.4f} V")
        if self.bias is not None and payload.get("bias_V") is not None:
            self.bias.update_value(payload["bias_V"])


class ImScanControls(QWidget):
    """Controls column beside the IM bias-scan plot (middle of the tab).

    Scan defaults sweep ±5 V in 0.2 V steps with a 1 s settle (a
    sub-second settle makes MMON repeat readings across consecutive
    points; at the EDFA27 commissioned 450 mW the photodetector clips
    above ~5.5 V — see docs/hardware/design.md). A finished scan shows
    suggested lock settings here (text only — the operator enters them
    in the servo panel above). Save writes the scan to CSV; Load ref
    overlays a saved calibration curve on the plot and is remembered
    across GUI restarts, like the OSA reference spectrum. The scan runs
    on the server's action executor, so it excludes the comb transitions
    and the shared Abort stops it; the pre-scan bias is restored when
    the sweep ends. A scan can only start while the lock is off (the
    server refuses otherwise).
    """

    def __init__(self, start_scan, abort_scan, save_scan, load_ref):
        super().__init__()
        self._start_scan = start_scan
        self.setMaximumWidth(280)

        # two-column grid keeps the panel short (the spectrum below needs
        # the vertical space): Start|Stop and Step|Settle share rows
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)

        def spin(value, low, high, step, decimals, units, tip) -> QDoubleSpinBox:
            box = SelectAllSpinBox()
            box.setRange(low, high)
            box.setDecimals(decimals)
            box.setSingleStep(step)
            box.setValue(value)
            box.setSuffix(f" {units}")
            box.setToolTip(tip)
            box.valueChanged.connect(self._refresh_estimate)
            return box

        # bounds mirror the server's ImScanRequest (±8 V, under the SIM960's ±10 V spec)
        self.v_start = spin(-5.0, -8, 8, 0.1, 3, "V", "scan start bias")
        self.v_stop = spin(5.0, -8, 8, 0.1, 3, "V", "scan stop bias")
        self.v_step = spin(0.2, 0.002, 0.5, 0.005, 3, "V", "bias step between points")
        self.settle_s = spin(1.0, 0.0, 5.0, 0.1, 2, "s", "settle time before each reading")
        for row, pairs in enumerate(
            (
                (("Start", self.v_start), ("Stop", self.v_stop)),
                (("Step", self.v_step), ("Settle", self.settle_s)),
            )
        ):
            for column, (label, box) in enumerate(pairs):
                grid.addWidget(QLabel(label), row, column * 2)
                grid.addWidget(box, row, column * 2 + 1)

        self.estimate = QLabel("")
        self.estimate.setStyleSheet("color: #5a6472;")
        grid.addWidget(self.estimate, 2, 0, 1, 4)
        self._refresh_estimate()

        buttons = QHBoxLayout()
        self.scan_button = QPushButton("Scan")
        self.scan_button.setToolTip("sweep the bias and plot the photodiode response")
        self.scan_button.clicked.connect(lambda: self._start_scan())
        self.abort_button = QPushButton("Abort")
        self.abort_button.setToolTip("stop the running scan (the bias is restored)")
        self.abort_button.setEnabled(False)
        self.abort_button.clicked.connect(lambda: abort_scan())
        save = QPushButton("Save")
        save.setToolTip("save the scan to a CSV file")
        save.clicked.connect(lambda: save_scan())
        ref = QPushButton("Load ref")
        ref.setToolTip(
            "overlay a saved calibration scan on the plot (remembered across GUI restarts)"
        )
        ref.clicked.connect(lambda: load_ref())
        for button in (self.scan_button, self.abort_button, save, ref):
            buttons.addWidget(button)
        grid.addLayout(buttons, 3, 0, 1, 4)

        self.recommend = QLabel(self.RECOMMEND_PLACEHOLDER)
        self.recommend.setWordWrap(True)
        self.recommend.setStyleSheet("color: #5a6472;")
        self.recommend.setToolTip(
            "suggested lock settings from the last scan — enter them in the "
            "servo panel above, then Lock (scan progress shows here too)"
        )
        # reserve two lines so text arriving doesn't reflow the whole tab
        self.recommend.setMinimumHeight(2 * self.recommend.fontMetrics().lineSpacing())
        grid.addWidget(self.recommend, 4, 0, 1, 4)
        grid.setRowStretch(5, 1)

    #: what the suggestion box says before any scan has run
    RECOMMEND_PLACEHOLDER = "run a scan to determine the lockpoint"

    def show_recommendation(self, rec: dict) -> None:
        """Display suggested lock settings from a finished scan (text
        only, deliberately: the operator types them into the servo panel)."""
        self.recommend.setText(
            f"suggest: lockpoint {rec['setpoint_V']:+.3f} V from bias "
            f"{rec['bias_V']:+.3f} V, P {rec['prop_gain']:+.1f}, I {rec['intg_gain']:g}"
        )

    def show_progress(self, text: str) -> None:
        """Scan progress shares the suggestion box (no separate status
        line on this tab); the suggestion replaces it when the scan ends."""
        self.recommend.setText(text)

    def show_placeholder(self) -> None:
        """Back to the resting text (aborted/unusable scan)."""
        self.recommend.setText(self.RECOMMEND_PLACEHOLDER)

    def params(self) -> dict:
        return {
            "v_start": self.v_start.value(),
            "v_stop": self.v_stop.value(),
            "v_step": self.v_step.value(),
            "settle_s": self.settle_s.value(),
        }

    def _refresh_estimate(self) -> None:
        span = self.v_stop.value() - self.v_start.value()
        n = max(int(span / self.v_step.value()), 0) if self.v_step.value() > 0 else 0
        # ~0.15 s/point of GPIB overhead on top of the settle time
        seconds = n * (self.settle_s.value() + 0.15)
        self.estimate.setText(f"{n} points, ~{seconds:.0f} s" if n else "empty range")

    def update_status(self, payload: dict) -> None:
        """Gate the Scan button on the run/lock state from the im_scan array."""
        running = bool(payload.get("running"))
        locked = payload.get("mode") == "PID"
        self.scan_button.setEnabled(not running and not locked)
        self.scan_button.setToolTip(
            "unlock the servo before scanning (the sweep would fight the PID)"
            if locked
            else "sweep the bias and plot the photodiode response"
        )
        self.abort_button.setEnabled(running)


class FlattenerSliderPanel(QWidget):
    """The flattener's ND-filter output slider (Thorlabs ELL12).

    Six slot buttons carrying the measured attenuations from the user
    guide (position 6 is the 0 dB reference); the current slot is
    highlighted from the GET/PUT read-back. The slider is not polled —
    the state refreshes after every command and via the Refresh button.
    Works fine with the slider not connected: the status line reports it
    and every command answers with the server's refusal.
    """

    #: measured insertion loss per slot (docs/user_guide/menlo_flattener.md)
    ATTENUATIONS = {
        1: "~5 dB",
        2: "~10 dB",
        3: "~20 dB",
        4: ">20 dB",
        5: ">20 dB",
        6: "0 dB",
    }

    def __init__(self, set_position, home, refresh):
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        slots = QHBoxLayout()
        self.position_buttons: dict[int, QPushButton] = {}
        for slot, atten in self.ATTENUATIONS.items():
            button = QPushButton(f"{slot}\n{atten}")
            button.setMaximumWidth(64)  # six of these must not set the window width
            if slot == 6:  # the button text already says slot + attenuation
                button.setToolTip("the 0 dB reference position")
            button.clicked.connect(lambda _checked, s=slot: set_position(s))
            self.position_buttons[slot] = button
            slots.addWidget(button)
        outer.addLayout(slots)

        status_row = QHBoxLayout()
        self.position_label = QLabel("position —")
        self.position_label.setToolTip(
            "current slot from the last read-back; — means unknown "
            "(between slots, not homed, or slider offline)"
        )
        status_row.addWidget(self.position_label)
        self.detail_label = QLabel("")
        self.detail_label.setStyleSheet("color: #5a6472; font-style: italic;")
        status_row.addWidget(self.detail_label, stretch=1)
        home_button = QPushButton("Home")
        home_button.setToolTip("re-reference the slider (needed once after a power-up)")
        home_button.clicked.connect(lambda: home())
        status_row.addWidget(home_button)
        refresh_button = QPushButton("Refresh")
        refresh_button.setToolTip("re-read the slider position (it is not polled)")
        refresh_button.clicked.connect(lambda: refresh())
        status_row.addWidget(refresh_button)
        outer.addLayout(status_row)

    def update_status(self, payload: dict) -> None:
        """Highlight the current slot from a GET/PUT read-back."""
        position = payload.get("position")
        self.detail_label.setText("")
        self.position_label.setText(f"position {position if position is not None else '—'}")
        for slot, button in self.position_buttons.items():
            active = position == slot
            button.setStyleSheet(f"color: {ACCENT}; font-weight: bold;" if active else "")

    def set_offline(self, detail: str) -> None:
        """Show why the slider is unreachable; the buttons stay live so a
        retry is one click (the server answers 503 with the reason)."""
        self.position_label.setText("position —")
        self.detail_label.setText(f"(not connected: {detail})")
        for button in self.position_buttons.values():
            button.setStyleSheet("")


class MainWindow(QMainWindow):
    def __init__(self, client: KeckogecoClient):
        super().__init__()
        self.client = client
        self.setWindowTitle("keckogeco — LFC engineering GUI")
        self.widgets: dict[str, object] = {}  # keyword -> widget
        self._action_status_shown = ""  # last action message sent to the status bar
        self._keyword_snapshot: dict = {}  # last /keywords poll (pre-flight checks)

        self.schema = client.schema()
        try:
            self.devices = client.devices()  # key -> {address, name, online, ...}
        except Exception:  # noqa: BLE001 - older server: titles just lose the port
            self.devices = {}

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

        # laptop health (this machine, not the rack): a local sampling
        # thread, no server involvement — the Laptop tab must keep working
        # while the server is down. Started after the layout exists.
        self.laptop_poller = LaptopPollThread()
        self.laptop_poller.sample_ready.connect(self._on_laptop_sample)
        self.laptop_poller.start()

    # ------------------------------------------------------------- building

    def _submit(self, keyword: str, value) -> None:
        if keyword in _CONFIRM and str(value) == "0":
            # power-off writes jump the FIFO write queue: an emergency
            # stop must not wait behind a slow in-flight write (the
            # Pritel's ~1 min power-amp ramp made Turn OFF appear to do
            # nothing; the server aborts the ramp when the OFF arrives)
            self.statusBar().showMessage(f"writing {keyword} = {value} (immediate) ...")
            self.writer.submit_urgent(keyword, value)
            return
        self.statusBar().showMessage(f"writing {keyword} = {value} ...")
        self.writer.submit(keyword, value)

    def _spec(self, keyword: str) -> dict:
        return self.schema.get(keyword, {})

    # Row tooltips are curated here, never inherited from the schema: the
    # legacy help strings ("set on or off rfosc powersup") read as noise
    # in a GUI whose rows already carry labels, and a control with nothing
    # non-obvious to say gets no tooltip at all (issue #37).

    def _add_spin(
        self,
        form: QFormLayout,
        label: str,
        keyword: str,
        submit=None,
        readback: bool = False,
        default: float | None = None,
        tooltip: str = "",
    ) -> None:
        """Add a setpoint box. ``readback`` shows the measured value beside
        it (the box then holds the setpoint only); ``default`` pre-fills the
        box — display only, a value is never auto-applied to hardware."""
        if keyword not in self.schema:
            return
        spec = {**self._spec(keyword), "help": tooltip}
        widget = KeywordSpinBox(keyword, spec, submit or self._submit, readback)
        if default is not None:
            widget.spin.blockSignals(True)
            widget.spin.setValue(default)
            widget.spin.blockSignals(False)
        self.widgets[keyword] = widget
        form.addRow(label, widget)

    def _add_display(self, form: QFormLayout, label: str, keyword: str, tooltip: str = "") -> None:
        if keyword not in self.schema:
            return
        widget = KeywordDisplay(keyword, {**self._spec(keyword), "help": tooltip})
        self.widgets[keyword] = widget
        form.addRow(label, widget)

    def _add_lamp_display(
        self, form: QFormLayout, label: str, keyword: str, ok, tooltip: str = ""
    ) -> None:
        if keyword not in self.schema:
            return
        widget = LampDisplay(keyword, {**self._spec(keyword), "help": tooltip}, ok, label=label)
        self.widgets[keyword] = widget
        form.addRow(label, widget)

    def _add_onoff(
        self, form: QFormLayout, label: str, keyword: str, submit=None, tooltip: str = ""
    ) -> None:
        if keyword not in self.schema:
            return
        widget = OnOffButton(
            keyword,
            {**self._spec(keyword), "help": tooltip},
            submit or self._submit,
            confirm=keyword in _CONFIRM,
            label=label,
        )
        self.widgets[keyword] = widget
        form.addRow(label, widget)

    def _build_layout(self) -> None:
        tabs = QTabWidget()
        tabs.addTab(self._overview_tab(), "Overview")
        tabs.addTab(self._im_lock_tab(), "IM Bias Lock")
        tabs.addTab(self._flattener_tab(), "Spectral Flattener")
        tabs.addTab(self._clock_tab(), "Clock")
        tabs.addTab(self._other_tab(), "Other")
        tabs.addTab(self._laptop_tab(), "Laptop")
        self.setCentralWidget(tabs)

    def _overview_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.addWidget(self._comb_state_panel())

        # two dense rows (Dan, 2026-07-17): a ~950 px window is fine, but
        # height is scarce — the window was clipping at the screen bottom
        row2 = QHBoxLayout()
        row2.addWidget(self._edfa_panel("Amonics EDFA 27 dBm", "LFC_EDFA27", "edfa27"), stretch=3)
        row2.addWidget(self._edfa_panel("Amonics EDFA 23 dBm", "LFC_EDFA23", "edfa23"), stretch=3)
        row2.addWidget(self._interlock_panel(), stretch=2)
        row2.addWidget(self._pritel_panel(), stretch=4)
        outer.addLayout(row2)

        row3 = QHBoxLayout()
        row3.addWidget(self._rf_panel())
        row3.addWidget(self._waveshaper_panel())
        row3.addWidget(self._temperature_panel(), stretch=1)
        outer.addLayout(row3)

        outer.addWidget(self._osa_panel(), stretch=1)
        return page

    def _im_lock_tab(self) -> QWidget:
        """Three sections, top to bottom: servo/lock controls with live
        strip charts, the bias scan, and a mirror of the OSA spectrum
        (users watch the comb while adjusting bias + RF attenuation).
        All start as placeholders; _on_arrays_available wires them when
        the server offers the im_scan / osa_spectrum arrays."""

        def placeholder(text: str) -> QLabel:
            label = QLabel(text)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setStyleSheet("color: #5a6472; font-style: italic; padding: 30px;")
            return label

        page = QWidget()
        layout = QVBoxLayout(page)

        servo_box = QGroupBox(self._title_with_port("IM bias servo (SIM960)", "srs"))
        self._im_servo_layout = QHBoxLayout(servo_box)
        self._im_servo_panel: ImServoPanel | None = None
        self._im_servo_placeholder = placeholder("(SRS SIM900 not connected)")
        self._im_servo_layout.addWidget(self._im_servo_placeholder, stretch=1)
        layout.addWidget(servo_box, stretch=2)

        scan_box = QGroupBox("Bias scan — photodiode vs bias (unlock first)")
        self._im_layout = QHBoxLayout(scan_box)
        self._im_plot = None
        self._im_controls: ImScanControls | None = None
        self._im_pos_dot = None
        self._im_ref_curve = None
        self._im_scan_was_running = False
        self._im_bias_start = None
        self._im_placeholder = placeholder("(SRS SIM900 not connected)")
        self._im_layout.addWidget(self._im_placeholder, stretch=1)
        # the scan is a utility view: keep it short (stretch 1 + a height
        # cap on its plot) so the spectrum below dominates the tab
        layout.addWidget(scan_box, stretch=1)

        osa_box = QGroupBox("Mini-comb spectrum (OSA) — controls on the Overview tab")
        self._im_osa_layout = QHBoxLayout(osa_box)
        self._im_osa_plot = None
        self._im_osa_placeholder = placeholder("(OSA not connected)")
        self._im_osa_layout.addWidget(self._im_osa_placeholder, stretch=1)
        layout.addWidget(osa_box, stretch=4)
        return page

    def _flattener_tab(self) -> QWidget:
        """Flattener hardware reachable from this laptop — for now just
        the ND-filter output slider. Built whether or not the slider is
        connected: offline shows as a status line, and commands surface
        the server's refusal in the status bar."""
        page = QWidget()
        layout = QVBoxLayout(page)

        box = QGroupBox(
            self._title_with_port(
                "Output attenuator — ND filter slider (Thorlabs ELL12)", "nd_slider"
            )
        )
        inner = QVBoxLayout(box)
        self._flattener_panel = FlattenerSliderPanel(
            self._flattener_set, self._flattener_home, self._flattener_refresh
        )
        inner.addWidget(self._flattener_panel)
        layout.addWidget(box)

        note = QLabel(
            "The SLM flattener itself (Flatten / Filter modes) runs on the Menlo "
            "laptop via Google Remote Desktop — see the user guide "
            "(docs/user_guide/menlo_flattener.md). Only the output ND-filter "
            "slider is controlled from here."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #5a6472;")
        layout.addWidget(note)
        layout.addStretch(1)

        device = self.devices.get("nd_slider", {})
        if device.get("online"):
            self._flattener_refresh()
        else:
            reason = device.get("offline_reason") or (
                "offline" if device else "no nd_slider block in the server config"
            )
            self._flattener_panel.set_offline(str(reason))
        return page

    def _laptop_tab(self) -> QWidget:
        """Health of the machine running this GUI (the comb laptop): ACPI
        zone temperatures with a strip chart, Windows' throttle response,
        CPU load/clock, and AC/battery state. Fan RPM is not readable
        without admin rights (see gui/laptop.py) — the throttle lamp is
        the fan-health proxy. Rows for the thermal zones are created when
        the first sample names them."""
        page = QWidget()
        outer = QVBoxLayout(page)
        box = QGroupBox(f"Laptop health — {platform.node() or 'this machine'}")
        row = QHBoxLayout(box)

        form = QFormLayout()
        self._laptop_form = form
        self._laptop_zone_rows: dict[str, QLabel] = {}
        self._laptop_status = QLabel("(waiting for the first sample ...)")
        self._laptop_status.setStyleSheet(f"color: {MUTED}; font-style: italic;")
        form.addRow(self._laptop_status)

        throttle_row = QWidget()
        throttle_layout = QHBoxLayout(throttle_row)
        throttle_layout.setContentsMargins(0, 0, 0, 0)
        self._laptop_throttle_lamp = StatusLamp("thermal throttling")
        self._laptop_throttle = QLabel("—")
        throttle_layout.addWidget(self._laptop_throttle_lamp)
        throttle_layout.addWidget(self._laptop_throttle)
        throttle_layout.addStretch(1)
        form.addRow("Thermal throttling", throttle_row)

        self._laptop_passive = QLabel("—")
        self._laptop_passive.setToolTip(
            "Windows' passive cooling limit: 100 % = full speed allowed; "
            "anything lower means the CPU is being slowed to shed heat"
        )
        form.addRow("Passive cooling limit", self._laptop_passive)
        self._laptop_cpu = QLabel("—")
        form.addRow("CPU load", self._laptop_cpu)
        self._laptop_clock = QLabel("—")
        self._laptop_clock.setToolTip(
            "effective clock vs the base frequency: >100 % = turbo, "
            "well under 100 % under load = throttled"
        )
        form.addRow("CPU clock", self._laptop_clock)
        self._laptop_power = QLabel("—")
        form.addRow("Power", self._laptop_power)
        row.addLayout(form)

        self._laptop_chart = self._plot_widget()
        self._laptop_curves: dict[str, object] = {}
        self._laptop_series: dict[str, tuple[list, list]] = {}
        if self._laptop_chart is not None:
            import pyqtgraph as pg

            self._laptop_chart.setLabel("left", "zone temperature (°C)")
            self._laptop_chart.setLabel("bottom", "time (min ago)")
            # minutes, not SI-scaled "x0.001 min" while history is short
            self._laptop_chart.getAxis("bottom").enableAutoSIPrefix(False)
            self._laptop_chart.addLegend(offset=(-10, 10), labelTextColor="#8b96a5")
            self._laptop_chart.addLine(
                y=TEMP_WARN_C, pen=pg.mkPen("#c78a00", style=Qt.PenStyle.DashLine)
            )
            row.addWidget(self._laptop_chart, stretch=1)
        outer.addWidget(box, stretch=1)

        note = QLabel(
            "Temperatures are the laptop's ACPI thermal zones (amber above "
            f"{TEMP_WARN_C:.0f} °C, red above {TEMP_HOT_C:.0f} °C). Fan RPM is not "
            "readable without admin rights on this machine, so the throttle lamp "
            "is the fan-health proxy: when cooling can't keep up, Windows slows "
            "the CPU (the passive cooling limit drops below 100 %) and the lamp "
            "turns red. A rising zone temperature plus throttling means check "
            "the fan and vents."
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {MUTED};")
        outer.addWidget(note)
        return page

    def _flattener_set(self, position: int) -> None:
        self.writer.submit_call("flattener", lambda c: c.flattener_slider_set(position))

    def _flattener_home(self) -> None:
        self.writer.submit_call("flattener", lambda c: c.flattener_slider_home())

    def _flattener_refresh(self) -> None:
        self.writer.submit_call("flattener", lambda c: c.flattener_slider())

    def _other_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)

        row1 = QHBoxLayout()
        row1.addWidget(self._edfa_panel("Amonics EDFA 13 dBm (not in use)", "LFC_EDFA13", "edfa13"))
        row1.addWidget(self._tec_panel())
        outer.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(self._shutter_panel())
        row2.addWidget(self._voa_panel(), stretch=1)
        outer.addLayout(row2)

        outer.addStretch(1)
        return page

    def _clock_tab(self) -> QWidget:
        """Reference-chain health: the FS725 Rb standard and the counter
        that should be disciplined by it. The DRO and the counter share
        the Rb 10 MHz, so a good LFC_REPRATE proves the whole chain —
        but a broken chain (counter on its internal timebase, 2026-07-17)
        reads ~200 Hz off with every other monitor green."""
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.addWidget(self._rb_clock_panel())
        outer.addWidget(self._pendulum_panel())
        outer.addStretch(1)
        return page

    def _rb_clock_panel(self) -> QGroupBox:
        box = QGroupBox(self._title_with_port("Rb frequency standard — SRS FS725", "rb_clock"))
        form = QFormLayout(box)
        self._add_lamp_display(
            form,
            "Phase lock",
            "LFC_RBCLOCK_PHASELOCK",
            ok=bool,
            tooltip="the 10 MHz output is phase-locked to the Rb transition",
        )
        self._add_lamp_display(
            form,
            "Frequency lock",
            "LFC_RBCLOCK_FREQLOCK",
            ok=bool,
            tooltip="the Rb frequency-lock loop is closed",
        )
        return box

    def _pendulum_panel(self) -> QGroupBox:
        """The comb repetition rate, measured — every digit the CNT-90XL
        resolves (LFC_REPRATE; em dash while the RF chain is off) — and
        the timebase the counter is actually using (green only on EXT,
        the Rb-disciplined rear 10 MHz input)."""
        box = QGroupBox(self._title_with_port("Repetition rate — Pendulum CNT-90XL", "pendulum"))
        layout = QVBoxLayout(box)
        display = PrecisionDisplay(
            "LFC_REPRATE",
            self._spec("LFC_REPRATE"),
            decimals=2,
            reference=16e9,  # the comb's design rep rate
            reference_label="Δ from 16 GHz",
        )
        self.widgets["LFC_REPRATE"] = display
        layout.addWidget(display)
        form = QFormLayout()
        self._add_lamp_display(
            form,
            "Timebase",
            "LFC_REPRATE_REF",
            ok=lambda v: str(v).strip().upper() == "EXT",
            tooltip="green only on EXT — the Rb-disciplined rear 10 MHz input; "
            "INT means the counter free-runs and the reading can't be trusted",
        )
        layout.addLayout(form)
        return box

    def _comb_state_panel(self) -> QGroupBox:
        # one compact row: banner, lamps, transition buttons. Transition
        # progress goes to the status bar (like the IM scan), not a label
        # here — a text area in the strip cost a whole row of height.
        box = QGroupBox("Comb State")
        outer = QHBoxLayout(box)

        self.state_banner = QLabel("UNKNOWN")
        self.state_banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.state_banner.setMinimumWidth(118)  # widest text: ENGINEERING MODE
        self._set_banner("UNKNOWN")
        outer.addWidget(self.state_banner)

        # lamp order per operations: RF chain first, then amplification
        self.subsystem_lamps: dict[str, StatusLamp] = {}
        lamps = QGridLayout()
        lamps.setHorizontalSpacing(6)
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
        outer.addStretch(1)

        for text, action in (
            ("STANDBY", "set_standby"),
            ("FULL COMB", "set_full_comb"),
            ("OFF", "set_off"),
        ):
            button = QPushButton(text)
            # the Windows style gives push buttons a ~140 px minimum;
            # four of those would set the whole window's width
            button.setMaximumWidth(84)
            button.setToolTip(
                f"step through the commissioned {text} power sequence "
                "(asks first; progress shows in the status bar)"
            )
            button.clicked.connect(lambda _checked, a=action, t=text: self._start_action(a, t))
            outer.addWidget(button)
        abort = QPushButton("Abort")
        abort.setMaximumWidth(84)
        abort.setToolTip("abort the running sequence (transitions and bias scans)")
        abort.clicked.connect(self._abort_action)
        outer.addWidget(abort)
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

    def _title_with_port(self, title: str, device_key: str) -> str:
        """Append the device's address, e.g. 'Interlock (COM4)'."""
        address = self.devices.get(device_key, {}).get("address")
        return f"{title} ({address})" if address else title

    #: commissioned EDFA23 ACC pump current, shown as the recommended value
    _EDFA23_RECOMMENDED_MA = 80.0

    def _edfa_panel(self, title: str, prefix: str, device_key: str = "") -> QGroupBox:
        box = QGroupBox(self._title_with_port(title, device_key))
        form = QFormLayout(box)
        self._add_onoff(form, "Emission", f"{prefix}_ONOFF")
        if prefix == "LFC_EDFA23":
            # runs in ACC: the setpoint is a pump current in mA. The last
            # value entered persists in [edfa23] in config/gui.toml and
            # pre-fills the box until the live poll takes over (display
            # only — a current is never auto-applied to an amplifier).
            self._add_spin(form, "Current", f"{prefix}_P", submit=self._edfa23_submit)
            widget = self.widgets.get(f"{prefix}_P")
            if widget is not None:
                saved = prefs.load_section("edfa23").get("setpoint_mA")
                if saved is not None:
                    widget.spin.blockSignals(True)
                    widget.spin.setValue(float(saved))
                    widget.spin.blockSignals(False)
                form.addRow(
                    "",
                    _hint_label(
                        f"recommended {self._EDFA23_RECOMMENDED_MA:g} mA",
                        "the commissioned ACC pump current",
                    ),
                )
        else:
            self._add_spin(form, "Setpoint", f"{prefix}_P")
        self._add_display(form, "Input power", f"{prefix}_INPUT_POWER_MONITOR")
        self._add_display(form, "Output power", f"{prefix}_OUTPUT_POWER_MONITOR")
        return box

    def _edfa23_submit(self, keyword: str, value) -> None:
        """EDFA23 current edits: write the keyword and remember the value."""
        self._submit(keyword, value)
        prefs.save_section("edfa23", {"setpoint_mA": float(value)})

    #: LFC_PTAMP_LATCH enum value meaning "ready to amplify" (schema enum:
    #: 1 ready, 0 stop-but-resettable, 3 too high, 5 too low, 4 unknown)
    _LATCH_READY = 1

    def _interlock_panel(self) -> QGroupBox:
        box = QGroupBox(self._title_with_port("Interlock", "arduino_relay"))
        form = QFormLayout(box)
        # lamp on the top row, like the Emission rows of the panels beside it
        self._add_lamp_display(
            form,
            "Latch",
            "LFC_PTAMP_LATCH",
            ok=lambda v: int(v) == self._LATCH_READY,
            tooltip="Pritel interlock latch — green means ready to amplify; "
            "after a trip, bring the photodiode voltage back in the window "
            "and press Reset",
        )
        self._add_display(
            form,
            "Voltage",
            "LFC_PTAMP_INTERLOCK_V",
            tooltip="interlock photodiode voltage (Arduino ADC) — green while "
            "inside the trip window below",
        )
        # trip window fetched once at startup (thresholds are quasi-static);
        # the live voltage is colored green/red against it in _on_keywords
        self._interlock_window: tuple[float, float] | None = None
        self._interlock_threshold = QLabel("—")
        self._interlock_threshold.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self._interlock_threshold.setToolTip("the latch trips outside this photodiode window")
        form.addRow("Window", self._interlock_threshold)
        reset = QPushButton("Reset latch")
        reset.clicked.connect(lambda: self._submit("LFC_PTAMP_LATCH", "1"))
        form.addRow("", reset)
        self.writer.submit_call("interlock", lambda c: c.interlock())
        return box

    #: commissioned Pritel bring-up currents (comb/actions.py _pritel_up)
    _PTAMP_PREAMP_MA = 600.0
    _PTAMP_PWRAMP_A = 3.9

    def _pritel_panel(self) -> QGroupBox:
        box = QGroupBox(self._title_with_port("Pritel amplifier", "ptamp"))
        form = QFormLayout(box)
        # "Emission" (not "Pump") to match the EDFA panels: one enable for
        # the whole box — it gates the power amp; the preamp is driven by
        # its current setpoint alone.
        self._add_onoff(
            form,
            "Emission",
            "LFC_PTAMP_ONOFF",
            submit=self._pritel_emission_submit,
            tooltip="gates the power amp; ON writes the Preamp setpoint first "
            "(the unit refuses to pump with the preamp at 0 mA)",
        )
        self._add_display(
            form,
            "Power after preamp",
            "LFC_PTAMP_IN",
            tooltip="power entering the power-amp stage (FA INPUT?) — mostly "
            "preamp ASE, so it does not prove good seed light",
        )
        # both currents read back 0 until emission is on, so these boxes
        # hold the setpoint (pre-filled with the commissioned bring-up
        # values) and show the measured current beside it
        self._add_spin(
            form, "Preamp", "LFC_PTAMP_PRE_P", readback=True, default=self._PTAMP_PREAMP_MA
        )
        self._add_spin(
            form, "Power amp", "LFC_PTAMP_I", readback=True, default=self._PTAMP_PWRAMP_A
        )
        self._add_display(form, "Output", "LFC_PTAMP_OUT")
        return box

    def _pritel_emission_submit(self, keyword: str, value) -> None:
        """Emission ON writes the preamp box's setpoint first. The unit's
        ASD refuses pump-on while the preamp is at 0 mA, and the box shows
        the bring-up default without ever having written it — clicking ON
        with the displayed value unapplied caused the 2026-07-15/17 "pump
        did not turn ON" refusals. The writer queue is FIFO, so the preamp
        write lands before the pump-on; if it fails, the ASD still blocks."""
        if str(value) == "1":
            preamp = self.widgets.get("LFC_PTAMP_PRE_P")
            if preamp is not None:
                self._submit("LFC_PTAMP_PRE_P", preamp.spin.value())
        self._submit(keyword, value)

    def _rf_panel(self) -> QGroupBox:
        box = QGroupBox("RF chain")
        form = QFormLayout(box)
        self._add_onoff(
            form,
            "Oscillator PSU",
            "LFC_RFOSCI_ONOFF",
            tooltip="power to the 16 GHz oscillator (Instek GPD-4303S channel 2)",
        )
        self._add_display(form, "Osc current", "LFC_RFOSCI_I")
        self._add_display(form, "Osc voltage", "LFC_RFOSCI_V")
        self._add_onoff(
            form,
            "Amplifier PSU",
            "LFC_RFAMP_ONOFF",
            tooltip="power to the RF amplifier (Instek GPP-1326 channel 1)",
        )
        self._add_display(form, "Amp current", "LFC_RFAMP_I")
        self._add_display(form, "Amp voltage", "LFC_RFAMP_V")
        return box

    def _temperature_panel(self) -> QGroupBox:
        """All thermocouple channels of both USB-2408 DAQ boards, fed by
        the LFC_TEMP_TEST1/2 array keywords (the seven LFC_T_* keywords
        stay bound server-side for KTL; here the full arrays cover them)."""
        box = QGroupBox("Temperatures")
        row = QHBoxLayout(box)
        for keyword, title, channels in _THERMO_PANELS:
            if keyword not in self.schema:
                continue
            column = QVBoxLayout()
            header = QLabel(title)
            header.setStyleSheet("font-weight: bold;")
            column.addWidget(header)
            array = ThermoArray(keyword, channels, _TEMP_TOLERANCE_C)
            self.widgets[keyword] = array
            column.addWidget(array)
            column.addStretch(1)
            row.addLayout(column, stretch=1)

        # the laptop lives in the same warm room: its hottest ACPI zone,
        # fed by the local health thread (details on the Laptop tab) and
        # colored by the absolute bands, not the rack's ±3 C baselines
        column = QVBoxLayout()
        header = QLabel("Laptop")
        header.setStyleSheet("font-weight: bold;")
        column.addWidget(header)
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        name = QLabel("CPU zone")
        self._overview_laptop_temp = QLabel("—")
        self._overview_laptop_temp.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        tip = (
            "hottest ACPI thermal zone of this laptop (details on the Laptop "
            f"tab) — amber above {TEMP_WARN_C:.0f} °C, red above {TEMP_HOT_C:.0f} °C"
        )
        name.setToolTip(tip)
        self._overview_laptop_temp.setToolTip(tip)
        grid.addWidget(name, 0, 0)
        grid.addWidget(self._overview_laptop_temp, 0, 1)
        column.addLayout(grid)
        column.addStretch(1)
        row.addLayout(column)
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
        if "osa_spectrum" in names and self._osa_plot is None:
            self._wire_osa_panel()
        if "osa_spectrum" in names and self._im_osa_plot is None:
            self._wire_im_osa_panel()
        if "im_scan" in names and self._im_plot is None:
            self._wire_im_panel()

    def _subscribe_array(self, name: str) -> None:
        if name not in self.poller.array_names:
            self.poller.array_names.append(name)

    def _plot_widget(self):
        """A themed pyqtgraph PlotWidget, or None if pyqtgraph is missing."""
        try:
            import pyqtgraph as pg
        except ImportError:
            return None
        plot = pg.PlotWidget()
        plot.setBackground(PLOT_BG)
        plot.showGrid(x=True, y=True, alpha=0.25)
        return plot

    def _wire_osa_panel(self) -> None:
        plot = self._plot_widget()
        if plot is None:
            self._osa_placeholder.setText("(pyqtgraph not installed; spectrum hidden)")
            return
        import pyqtgraph as pg

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
        self._subscribe_array("osa_spectrum")
        # per Dan: connecting means "show me the standard mini-comb view",
        # so the defaults are applied to the OSA, not just displayed
        self._osa_controls.apply_defaults()
        self._restore_reference()

    def _wire_im_osa_panel(self) -> None:
        """Spectrum-only mirror of the OSA on the IM tab: same array, its
        own plot; view + sweep settings stay on the Overview tab."""
        plot = self._plot_widget()
        if plot is None:
            self._im_osa_placeholder.setText("(pyqtgraph not installed; spectrum hidden)")
            return
        import pyqtgraph as pg

        curve = plot.plot(pen=pg.mkPen(ACCENT, width=1))
        self._im_osa_plot = (plot, curve)
        self._im_osa_placeholder.deleteLater()
        self._im_osa_layout.addWidget(plot, stretch=1)
        self._subscribe_array("osa_spectrum")

    def _wire_im_panel(self) -> None:
        plot = self._plot_widget()
        if plot is None:
            self._im_placeholder.setText("(pyqtgraph not installed; scan plot hidden)")
            self._im_servo_placeholder.setText("(pyqtgraph not installed)")
            return
        import pyqtgraph as pg

        # --- middle: the scan plot + controls (kept short deliberately)
        plot.setMaximumHeight(230)
        plot.setLabel("bottom", "IM bias (V)")
        plot.setLabel("left", "photodiode (V)")
        curve = plot.plot(
            pen=pg.mkPen(ACCENT, width=1),
            symbol="o",
            symbolSize=4,
            symbolBrush=ACCENT,
            symbolPen=None,
        )
        self._im_plot = (plot, curve)
        # purple dot: where the servo currently sits on the transfer curve
        # (current bias out vs photodiode), refreshed by the im_scan array
        self._im_pos_dot = plot.plot(
            [], [], pen=None, symbol="o", symbolSize=10, symbolBrush="#c678dd", symbolPen=None
        )
        self._im_pos_dot.setZValue(1)  # above the scan trace
        self._im_ref_curve = None  # dashed overlay, created on first load
        self._im_placeholder.deleteLater()
        self._im_layout.addWidget(plot, stretch=1)
        self._im_controls = ImScanControls(
            self._im_scan_start, self._abort_action, self._im_save, self._im_load_ref
        )
        self._im_layout.addWidget(self._im_controls)
        self._im_restore_reference()

        # --- top: lock controls + photodiode / bias strip charts
        # every editable box steps 0.01 per arrow click (Dan, 2026-07-16)
        # and applies arrow steps live (issue #41: stepping the bias did
        # nothing until focus left the box)
        def keyword_spin(keyword: str, help_text: str = "") -> KeywordSpinBox | None:
            if keyword not in self.schema:
                return None
            spec = {**self._spec(keyword), "step": 0.01, "help": help_text}
            widget = KeywordSpinBox(keyword, spec, self._submit, live=True)
            self.widgets[keyword] = widget
            return widget

        setpoint = KeywordSpinBox(
            "setpoint_V",
            {
                "units": "V",
                "min": -10,
                "max": 10,
                "step": 0.01,
                "help": "lockpoint — the photodiode voltage the PID holds "
                "(adjustable while locked)",
            },
            lambda _f, value: self._im_apply(setpoint_V=value),
            live=True,
        )
        prop = KeywordSpinBox(
            "prop_gain",
            {
                "min": -1000,
                "max": 1000,
                "step": 0.01,
                "help": "SIM960 proportional gain — the sign sets the feedback "
                "polarity and must match the fringe slope's sign",
            },
            lambda _f, value: self._im_apply(prop_gain=value),
            live=True,
        )
        intg = KeywordSpinBox(
            "intg_gain",
            {
                "min": 0.01,
                "max": 500000,
                "step": 0.01,
                "help": "SIM960 integral gain (1/s)",
            },
            lambda _f, value: self._im_apply(intg_gain=value),
            live=True,
        )
        self._im_bias_start = KeywordSpinBox(
            "bias_start",
            {
                "units": "V",
                "min": -8,
                "max": 8,
                "step": 0.01,
                "help": "bias the lock starts from — Lock writes it to LFC_IM_BIAS "
                "before engaging (remembered across GUI restarts)",
            },
            lambda _f, value: prefs.save_section("im_lock", {"bias_start": value}),
            live=True,
        )
        saved_start = prefs.load_section("im_lock").get("bias_start")
        if saved_start is not None:
            self._im_bias_start.update_value(saved_start)
        bias = keyword_spin(
            "LFC_IM_BIAS",
            "bias applied to the IM — editable while unlocked; while locked "
            "it is read-only and follows the PID's live output",
        )
        # the Bias out box is fed exclusively by the im_scan array (OMON,
        # the live output). The keyword snapshot reports MOUT — the manual
        # setting, which differs from OMON while the PID drives the
        # output, so double-feeding made the box flip between two values.
        self.widgets.pop("LFC_IM_BIAS", None)
        self._im_servo_panel = ImServoPanel(
            self._im_set_lock,
            bias_widget=bias,
            rf_att_widget=keyword_spin(
                "LFC_IM_RF_ATT",
                "drive voltage of the mini-comb RF attenuator (VCA) — iterate "
                "against the bias while flattening the mini-comb",
            ),
            setpoint_widget=setpoint,
            prop_widget=prop,
            intg_widget=intg,
            bias_start_widget=self._im_bias_start,
        )
        self._im_servo_placeholder.deleteLater()
        self._im_servo_layout.addWidget(self._im_servo_panel)
        # populate the gain boxes (and mode) from the live servo: an empty
        # im_apply is a read — gains are not in the polled array payload
        self.writer.submit_call("IM settings", lambda c: c.im_apply())

        self._im_history: dict[str, list] = {"t": [], "pd": [], "bias": []}
        self._im_charts = []
        for label, key in (("photodiode (V)", "pd"), ("bias out (V)", "bias")):
            chart = self._plot_widget()
            chart.setLabel("left", label)
            chart.setLabel("bottom", "time (s ago)")
            chart_curve = chart.plot(pen=pg.mkPen(ACCENT, width=1))
            self._im_charts.append((key, chart, chart_curve))
            self._im_servo_layout.addWidget(chart, stretch=1)

        # every poll cycle: the strip charts sample at ~1 Hz and scans
        # build up live (the payload is cached data during a sweep)
        self.poller.array_every["im_scan"] = 1
        self._subscribe_array("im_scan")

    #: strip-chart history length, seconds (at the ~1 s poll cadence)
    IM_HISTORY_S = 600

    def _im_record_history(self, payload: dict) -> None:
        if payload.get("input_V") is None or payload.get("bias_V") is None:
            return  # mid-scan or readout failed: leave a gap
        history = self._im_history
        now = time.time()
        history["t"].append(now)
        history["pd"].append(payload["input_V"])
        history["bias"].append(payload["bias_V"])
        while history["t"] and history["t"][0] < now - self.IM_HISTORY_S:
            for series in history.values():
                series.pop(0)
        ages = [t - now for t in history["t"]]
        for key, _chart, curve in self._im_charts:
            curve.setData(ages, history[key])

    def _im_set_lock(self, engage: bool) -> None:
        if engage and self._im_bias_start is not None:
            # start the lock from the Bias start box: the write queue is
            # FIFO, so the bias lands before the engage copies it into
            # the SIM960 output offset
            self._submit("LFC_IM_BIAS", self._im_bias_start.spin.value())
        self._submit("LFC_IM_LOCK_MODE", "1" if engage else "0")

    def _im_apply(self, **settings) -> None:
        self.writer.submit_call("IM settings", lambda c: c.im_apply(**settings))

    def _im_scan_start(self) -> None:
        # never sweep under the Pritel (issue #43): the sweep crosses
        # fringe nulls that starve the amplifier's seed. The server
        # refuses too; this check gives the operator a dialog up front.
        if self._keyword_snapshot.get("LFC_PTAMP_ONOFF", {}).get("value"):
            QMessageBox.warning(
                self,
                "Pritel is on",
                "Turn off the Pritel amplifier before scanning the IM bias.\n\n"
                "The sweep crosses bias points that starve the amplifier's seed.",
            )
            return
        params = self._im_controls.params()
        self.writer.submit_call("IM scan", lambda c: c.im_scan(**params))

    def _im_recommend_if_scan_finished(self, data: dict) -> None:
        """A running->stopped edge on the im_scan array means a sweep just
        ended: analyze it and show suggested lock settings (text only —
        the operator enters them in the servo panel)."""
        running = bool(data.get("running"))
        finished = self._im_scan_was_running and not running
        self._im_scan_was_running = running
        if not finished or self._im_controls is None:
            return
        try:
            # suggest the mid-fringe crossing nearest the previous
            # lock-start bias, so the suggestion stays on "our" fringe
            start = self._im_bias_start
            near = start.spin.value() if start is not None else None
            rec = recommend_lock_point(data.get("x") or [], data.get("y") or [], near_bias=near)
        except ValueError as exc:
            # e.g. an aborted sweep: clear the stale progress text
            self._im_controls.show_placeholder()
            self.statusBar().showMessage(f"no lock-setting suggestion: {exc}", 10000)
            return
        self._im_controls.show_recommendation(rec)

    def _im_set_ref_curve(self, x: list, y: list) -> None:
        """Show (or update) the dashed reference-calibration overlay."""
        if self._im_ref_curve is None:
            import pyqtgraph as pg

            plot, _live = self._im_plot
            pen = pg.mkPen("#b085f5", width=1, style=Qt.PenStyle.DashLine)
            self._im_ref_curve = plot.plot(pen=pen, name="reference")
            self._im_ref_curve.setZValue(-1)  # behind the live scan
        self._im_ref_curve.setData(x, y)

    def _im_load_ref(self) -> None:
        """Pick a saved scan CSV as the reference calibration curve; the
        choice is remembered in the GUI prefs (like the OSA reference)."""
        path, _filter = QFileDialog.getOpenFileName(
            self, "Load reference calibration scan", str(self._spectra_dir()), "CSV files (*.csv)"
        )
        if not path:
            return
        try:
            x, y, _metadata = spectra.load_spectrum_csv(path)
        except (OSError, ValueError) as exc:
            self.statusBar().showMessage(f"LOAD FAILED: {exc}", 10000)
            return
        self._im_set_ref_curve(x, y)
        prefs.save_section("im_reference", {"csv": Path(path).as_posix()})
        self.statusBar().showMessage(f"IM reference calibration: {path}", 8000)

    def _im_restore_reference(self) -> None:
        """Re-display the saved reference calibration scan, if any."""
        saved = prefs.load_section("im_reference").get("csv")
        if not saved:
            return
        try:
            x, y, _metadata = spectra.load_spectrum_csv(saved)
        except (OSError, ValueError) as exc:
            self.statusBar().showMessage(f"IM reference not restored: {exc}", 10000)
            return
        self._im_set_ref_curve(x, y)

    def _im_save(self) -> None:
        data = getattr(self, "_im_data", None)
        if not data or not data.get("x"):
            self.statusBar().showMessage("no scan data to save yet", 5000)
            return
        default = self._spectra_dir() / time.strftime("im_scan_%Y-%m-%d_%H%M%S.csv")
        path, _filter = QFileDialog.getSaveFileName(
            self, "Save IM bias scan", str(default), "CSV files (*.csv)"
        )
        if not path:
            return
        metadata = dict(self._im_controls.params())
        metadata["x_label"] = data.get("x_label", "")
        metadata["y_label"] = data.get("y_label", "")
        metadata["points"] = len(data["x"])
        try:
            spectra.save_spectrum_csv(path, data["x"], data["y"], metadata)
        except OSError as exc:
            self.statusBar().showMessage(f"SAVE FAILED: {exc}", 10000)
            return
        self.statusBar().showMessage(f"saved {path}", 8000)

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
        if label == "IM scan":
            self.statusBar().showMessage("IM bias scan started", 5000)
            return
        if label == "IM settings":
            if self._im_servo_panel is not None and isinstance(result, dict):
                self._im_servo_panel.update_status(result)  # PUT read-back
            return
        if not isinstance(result, dict):
            return
        if label == "flattener":
            self._flattener_panel.update_status(result)
            return
        if label == "interlock":
            low = result.get("low_threshold_V")
            high = result.get("high_threshold_V")
            if low is not None and high is not None:
                self._interlock_window = (float(low), float(high))
                self._interlock_threshold.setText(f"{low:.2f} – {high:.2f} V")
            return
        if self._osa_controls is None:
            return
        if label == "OSA settings":
            self._osa_controls.populate(result)
        elif label == "OSA sweep":
            self._osa_controls.set_sweep(result.get("sweep_continuous"))

    #: commissioned dispersion, from the old orchestration; also the
    #: initial [wsp] values shipped in config/gui.toml
    _WSP_RECOMMENDED = {
        "LFC_WSP_PHASE": 2.14,
        "LFC_WSP_TOD": 0.0,
        "LFC_WSP_CENTER": 1559.8,
    }
    _WSP_PREF_KEYS = {
        "LFC_WSP_PHASE": "gdd_ps_nm",
        "LFC_WSP_TOD": "tod_ps_nm2",
        "LFC_WSP_CENTER": "center_nm",
    }

    def _waveshaper_panel(self) -> QGroupBox:
        """GDD / TOD / center boxes. The values persist in the GUI prefs
        ([wsp] in config/gui.toml) and are re-applied to the instrument
        when the GUI starts, since the server's softstore forgets them on
        a restart."""
        box = QGroupBox("WaveShaper dispersion")
        form = QFormLayout(box)
        saved = prefs.load_section("wsp")
        remembered = {
            kw: float(saved.get(pref_key, self._WSP_RECOMMENDED[kw]))
            for kw, pref_key in self._WSP_PREF_KEYS.items()
        }
        self._wsp_spins: dict[str, KeywordSpinBox] = {}
        for label, keyword in (
            ("GDD (d2)", "LFC_WSP_PHASE"),
            ("TOD (d3)", "LFC_WSP_TOD"),
            ("Center", "LFC_WSP_CENTER"),
        ):
            if keyword not in self.schema:
                continue
            # 0.01 per arrow click, like the IM lock boxes (Dan, 2026-07-17)
            widget = KeywordSpinBox(
                keyword,
                {
                    **self._spec(keyword),
                    "step": 0.01,
                    "help": "the three boxes program the WaveShaper together "
                    "as one phase profile (remembered across GUI restarts)",
                },
                self._wsp_submit,
            )
            widget.spin.setValue(remembered[keyword])
            self._wsp_spins[keyword] = widget
            self.widgets[keyword] = widget
            form.addRow(label, widget)
        if self._wsp_spins:
            recommended = QPushButton("2.14 / 0.00 / 1559.8")
            recommended.setToolTip(
                "apply the commissioned dispersion: d2 = 2.14 ps/nm, "
                "d3 = 0 ps/nm², centered at 1559.8 nm"
            )
            recommended.clicked.connect(lambda: self._apply_wsp(self._WSP_RECOMMENDED))
            form.addRow("Recommended", recommended)
        # restore the remembered profile onto the instrument (skipped when
        # the WaveShaper is offline: the keywords report as unbound)
        if self._wsp_spins and all(self.schema.get(kw, {}).get("bound") for kw in self._wsp_spins):
            self._apply_wsp(remembered)
        return box

    def _apply_wsp(self, values: dict) -> None:
        for keyword, value in values.items():
            widget = self._wsp_spins.get(keyword)
            if widget is None:
                continue
            widget.spin.blockSignals(True)
            widget.spin.setValue(value)
            widget.spin.blockSignals(False)
            self._submit(keyword, value)
        self._save_wsp_prefs()

    def _wsp_submit(self, keyword: str, value) -> None:
        """Spin-box edits: write the keyword and remember the trio."""
        self._submit(keyword, value)
        self._save_wsp_prefs()

    def _save_wsp_prefs(self) -> None:
        prefs.save_section(
            "wsp",
            {
                self._WSP_PREF_KEYS[keyword]: widget.spin.value()
                for keyword, widget in self._wsp_spins.items()
            },
        )

    def _tec_panel(self) -> QGroupBox:
        # the IM bias control moved to the IM Bias Lock tab (one widget per
        # keyword: a duplicate spin box here would stop getting updates)
        box = QGroupBox("TECs")
        form = QFormLayout(box)
        ramp_note = "applied in 0.5 °C steps to avoid thermal shock to the crystal"
        self._add_spin(form, "PPLN temp", "LFC_PPLN_T", tooltip=ramp_note)
        self._add_spin(form, "Waveguide temp", "LFC_WGD_T", tooltip=ramp_note)
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
        form = QFormLayout(box)  # one unit per row: side by side was 700 px wide
        for serial, keyword in [
            ("NO-303699-01", "LFC_VOA1550_ATTEN"),
            ("NO-303700-01", "LFC_VOA1310_ATTEN"),
            ("NO-311029-01", "LFC_VOA2000_ATTEN"),
        ]:
            if keyword not in self.schema:
                continue
            self._add_spin(
                form,
                serial,
                keyword,
                tooltip="attenuation reads unknown (—) until the first set "
                "after power-up homes the unit",
            )
        return box

    # --------------------------------------------------------------- slots

    def _on_keywords(self, snapshot: dict) -> None:
        self._keyword_snapshot = snapshot
        for keyword, payload in snapshot.items():
            widget = self.widgets.get(keyword)
            if widget is not None and hasattr(widget, "update_value"):
                widget.update_value(payload["value"])
        # the IM lock lamp is keyword-driven (servo PID mode), not in /state
        im_lock = snapshot.get("LFC_IM_LOCK_MODE")
        if im_lock is not None:
            value = im_lock.get("value")
            self.subsystem_lamps["im_lock"].set_state(None if value is None else bool(value))
        volts = snapshot.get("LFC_PTAMP_INTERLOCK_V")
        if volts is not None:
            self._color_interlock_voltage(volts.get("value"))

    def _color_interlock_voltage(self, value) -> None:
        """Green when the photodiode voltage is inside the trip window,
        red outside; plain while the window (or value) is unknown."""
        widget = self.widgets.get("LFC_PTAMP_INTERLOCK_V")
        if widget is None:
            return
        if value is None or self._interlock_window is None:
            widget.setStyleSheet("")
            return
        low, high = self._interlock_window
        ok = low < float(value) < high
        widget.setStyleSheet(f"color: {'#35d07f' if ok else '#e05252'}; font-weight: bold;")

    def _on_state(self, state: dict) -> None:
        self._set_banner(state.get("state", "UNKNOWN"))
        for key, lamp in self.subsystem_lamps.items():
            if key == "im_lock":
                continue  # driven from the keyword snapshot instead
            lamp.set_state(state.get("subsystems", {}).get(key))
        action = state.get("action")
        if action and action.get("running"):
            text = (
                f"⏳ {action['name']} — step {action['step']}"
                + (f"/{action['total_steps']}" if action.get("total_steps") else "")
                + f": {action['message']}"
            )
        elif action and action.get("error"):
            text = f"❌ {action['name']}: {action['error']}"
        elif action:
            text = f"✓ last action {action['name']}: {action['message']}"
        else:
            text = ""
        # All action progress goes to the status bar (a dedicated label
        # cost a row of the comb-state strip); IM scans additionally show
        # in the scan panel's suggestion box while running (the suggestion
        # replaces it when the sweep ends).
        is_im = bool(action) and str(action.get("name", "")).startswith("im_")
        if is_im and action.get("running") and self._im_controls is not None:
            self._im_controls.show_progress(text)
        if text and text != self._action_status_shown:
            # persists while running (refreshed as the message advances);
            # the final ✓/❌ shows once, then times out
            self.statusBar().showMessage(text, 0 if action.get("running") else 8000)
        self._action_status_shown = text

    def _on_array(self, name: str, data: dict) -> None:
        if name == "osa_spectrum":
            if self._osa_plot is not None:
                self._osa_data = data  # kept for "Save"
                plot, curve = self._osa_plot
                curve.setData(data.get("x", []), data.get("y", []))
                plot.setLabel("bottom", data.get("x_label", ""))
                plot.setLabel("left", data.get("y_label", ""))
            if self._im_osa_plot is not None:
                _plot, curve = self._im_osa_plot
                curve.setData(data.get("x", []), data.get("y", []))
        elif name == "im_scan" and self._im_plot is not None:
            self._im_data = data  # kept for "Save"
            _plot, curve = self._im_plot
            curve.setData(data.get("x", []), data.get("y", []))
            if self._im_controls is not None:
                self._im_controls.update_status(data)
            if self._im_servo_panel is not None:
                self._im_servo_panel.update_status(data)
                self._im_record_history(data)
            # purple dot: where the servo sits on the curve right now.
            # Mid-sweep the payload has no live readouts (the poller
            # doesn't compete with the scan for the mainframe), but the
            # newest sweep point IS the current position
            if self._im_pos_dot is not None:
                if data.get("running") and data.get("x") and data.get("y"):
                    self._im_pos_dot.setData([data["x"][-1]], [data["y"][-1]])
                elif data.get("bias_V") is not None and data.get("input_V") is not None:
                    self._im_pos_dot.setData([data["bias_V"]], [data["input_V"]])
            self._im_recommend_if_scan_finished(data)

    def _on_connection(self, ok: bool, detail: str) -> None:
        if ok:
            self.statusBar().showMessage(f"connected to {self.client.base_url}")
        else:
            self.statusBar().showMessage(f"NOT CONNECTED: {detail}")

    def _on_write_ok(self, keyword: str, value) -> None:
        self.statusBar().showMessage(f"{keyword} = {value}", 5000)

    def _on_write_failed(self, keyword: str, error: str) -> None:
        self.statusBar().showMessage(f"WRITE FAILED {keyword}: {error}", 10000)
        if keyword == "IM scan":
            # the server's refusal (Pritel on, lock engaged, ...) deserves
            # a dialog, not just a status-bar line the operator may miss
            QMessageBox.warning(self, "Scan refused", error)
        if keyword == "flattener":
            self._flattener_panel.set_offline(error)
        # let the next poll snap the box back to the instrument's value
        widget = self.widgets.get(keyword)
        if hasattr(widget, "write_rejected"):
            widget.write_rejected()

    #: laptop temperature strip-chart history, seconds (~2 s cadence)
    LAPTOP_HISTORY_S = 1800

    _LAPTOP_ZONE_COLORS = (ACCENT, "#c678dd", "#5b9bd5", "#c78a00")

    def _on_laptop_sample(self, sample) -> None:
        """One LaptopSample from the local health thread (gui/laptop.py)."""
        if sample.error:
            self._laptop_status.setText(f"(health sensors unavailable: {sample.error})")
            self._laptop_status.show()
            self._overview_laptop_temp.setText("—")
            self._overview_laptop_temp.setStyleSheet("")
            return
        self._laptop_status.hide()

        # Overview's Temperatures panel mirrors the hottest zone
        if sample.zones_C:
            hottest = max(sample.zones_C.values())
            self._overview_laptop_temp.setText(f"{hottest:.1f} °C")
            self._overview_laptop_temp.setStyleSheet(
                _LAPTOP_TEMP_STYLES.get(temp_state(hottest), "")
            )
        else:
            self._overview_laptop_temp.setText("—")
            self._overview_laptop_temp.setStyleSheet("")

        for zone in sorted(sample.zones_C):
            if zone not in self._laptop_zone_rows:
                label = QLabel("—")
                label.setToolTip(
                    f"ACPI thermal zone {zone} — amber above {TEMP_WARN_C:.0f} °C, "
                    f"red above {TEMP_HOT_C:.0f} °C"
                )
                # zones stack at the top of the form, in sorted order
                self._laptop_form.insertRow(len(self._laptop_zone_rows), f"Zone {zone}", label)
                self._laptop_zone_rows[zone] = label
            label = self._laptop_zone_rows[zone]
            temp = sample.zones_C[zone]
            label.setText(f"{temp:.1f} °C")
            label.setStyleSheet(_LAPTOP_TEMP_STYLES.get(temp_state(temp), ""))

        details = []
        codes = {zone: int(v) for zone, v in sample.throttle.items() if v}
        if codes:
            details.append("reasons " + ", ".join(f"{z}: {c}" for z, c in codes.items()))
        passive = min(sample.passive_limit_pct.values(), default=None)
        if passive is not None and passive < 100.0:
            details.append(f"passive limit {passive:.0f} %")
        if sample.throttling:
            self._laptop_throttle_lamp.set_state("fault")
            self._laptop_throttle.setText("THROTTLING — " + "; ".join(details))
            self._laptop_throttle.setStyleSheet("color: #e05252; font-weight: bold;")
        else:
            self._laptop_throttle_lamp.set_state(True)
            self._laptop_throttle.setText("none")
            self._laptop_throttle.setStyleSheet("")
        self._laptop_passive.setText("—" if passive is None else f"{passive:.0f} %")

        util, perf = sample.cpu_util_pct, sample.cpu_perf_pct
        self._laptop_cpu.setText("—" if util is None else f"{util:.0f} %")
        self._laptop_clock.setText("—" if perf is None else f"{perf:.0f} % of base")

        battery = "" if sample.battery_pct is None else f" ({sample.battery_pct:.0f} %)"
        if sample.ac_power is False:  # unplugged: the comb GUI dies with the battery
            self._laptop_power.setText(f"ON BATTERY{battery}")
            self._laptop_power.setStyleSheet("color: #e05252; font-weight: bold;")
        else:
            self._laptop_power.setText("—" if sample.ac_power is None else f"AC{battery}")
            self._laptop_power.setStyleSheet("")

        if self._laptop_chart is None:
            return
        import pyqtgraph as pg

        now = time.time()
        for zone, temp in sample.zones_C.items():
            if zone not in self._laptop_series:
                color = self._LAPTOP_ZONE_COLORS[
                    len(self._laptop_series) % len(self._LAPTOP_ZONE_COLORS)
                ]
                self._laptop_curves[zone] = self._laptop_chart.plot(
                    pen=pg.mkPen(color, width=1), name=zone
                )
                self._laptop_series[zone] = ([], [])
            times, temps = self._laptop_series[zone]
            times.append(now)
            temps.append(temp)
            while times and times[0] < now - self.LAPTOP_HISTORY_S:
                times.pop(0)
                temps.pop(0)
            self._laptop_curves[zone].setData([(t - now) / 60.0 for t in times], temps)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self.poller.stop()
        self.writer.stop()
        self.laptop_poller.stop()
        super().closeEvent(event)
