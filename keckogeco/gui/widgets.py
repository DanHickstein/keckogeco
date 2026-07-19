"""Shared widgets for the engineering GUI.

Every control is keyed by its KTL keyword; units, limits, and writability
come from the server's ``/schema`` endpoint, so the GUI never hardcodes a
range that the schema (and therefore Keck) doesn't agree with.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QWidget,
)

__all__ = [
    "KeywordDisplay",
    "KeywordSpinBox",
    "LampDisplay",
    "OnOffButton",
    "PrecisionDisplay",
    "SelectAllSpinBox",
    "StatusLamp",
    "ThermoArray",
    "format_value",
]


def format_value(value, units: str = "") -> str:
    """Render a keyword value the way the GUI's readouts show it."""
    if value is None:  # server reports unknown (e.g. VOA not homed) as null
        return "—"
    if isinstance(value, bool):
        text = "ON" if value else "OFF"
    elif isinstance(value, float):
        text = f"{value:.3f}"
    else:
        text = str(value)
    return f"{text} {units}".strip()


class SelectAllSpinBox(QDoubleSpinBox):
    """QDoubleSpinBox that selects its value on focus, so clicking into
    the box and typing replaces the number immediately."""

    def focusInEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().focusInEvent(event)
        # deferred: Qt would otherwise clear the selection right after
        # this handler when the mouse press places the cursor
        QTimer.singleShot(0, self.selectAll)


_LAMP_COLORS = {
    True: "#35d07f",  # green
    False: "#3a4350",  # grey (off)
    "fault": "#e05252",  # red
    None: "#5a6472",  # unknown
}


class StatusLamp(QLabel):
    """Small round indicator: green (on/ok), grey (off), red (fault)."""

    def __init__(self, label: str = ""):
        super().__init__()
        self._label = label
        self.setFixedSize(16, 16)
        self.set_state(None)

    def set_state(self, state) -> None:
        color = _LAMP_COLORS.get(state, _LAMP_COLORS[None])
        self.setStyleSheet(
            f"background-color: {color}; border-radius: 8px; border: 1px solid #0b0e13;"
        )
        state_name = {True: "ON", False: "OFF", None: "?"}.get(state, str(state))
        self.setToolTip(f"{self._label}: {state_name}")


class PrecisionDisplay(QLabel):
    """Large digit-grouped readout for a high-resolution keyword (the
    Pendulum rep rate: a 0.1 s gate on the CNT-90XL resolves ~12 digits
    at 16 GHz, and every one of them deserves to be visible).

    Shows the full value with thin-space digit grouping in a big
    monospace face; ``reference`` adds a small second line with the
    offset from that value (e.g. Δ from 16 GHz). NaN/None (RF chain
    off, counter unavailable) shows an em dash.
    """

    def __init__(
        self,
        keyword: str,
        spec: dict,
        decimals: int = 2,
        reference: float | None = None,
        reference_label: str = "",
    ):
        super().__init__("—")
        self.keyword = keyword
        self.units = spec.get("units", "")
        self.decimals = decimals
        self.reference = reference
        self.reference_label = reference_label
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if spec.get("help"):  # no tooltip beats a raw keyword name
            self.setToolTip(spec["help"])
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setStyleSheet("font-family: Consolas, 'Courier New', monospace;")

    def update_value(self, value) -> None:
        if value is None or (isinstance(value, float) and not math.isfinite(value)):
            self.setText("—")
            return
        value = float(value)
        grouped = f"{value:,.{self.decimals}f}".replace(",", "&thinsp;")
        text = f'<span style="font-size: 26px; color: #4fd1c5;">{grouped} {self.units}</span>'
        if self.reference is not None:
            delta = value - self.reference
            text += (
                f'<br><span style="font-size: 12px; color: #8b96a5;">'
                f"{self.reference_label}: {delta:+,.{self.decimals}f} {self.units}</span>"
            )
        self.setText(text)


class KeywordDisplay(QLabel):
    """Read-only value display for one keyword."""

    def __init__(self, keyword: str, spec: dict):
        super().__init__("—")
        self.keyword = keyword
        self.units = spec.get("units", "")
        self.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if spec.get("help"):
            self.setToolTip(spec["help"])

    def update_value(self, value) -> None:
        self.setText(format_value(value, self.units))


class LampDisplay(QWidget):
    """Status lamp + read-only value for a keyword with no on/off button.

    Gives a status row (e.g. the interlock latch) the same lamp-then-value
    shape as the ``OnOffButton`` rows beside it. ``ok`` maps the keyword
    value to the lamp state: green when it returns True, grey otherwise.
    """

    def __init__(self, keyword: str, spec: dict, ok: Callable[[object], bool], label: str = ""):
        super().__init__()
        self.keyword = keyword
        self._ok = ok
        self.lamp = StatusLamp(label or keyword)
        self.display = KeywordDisplay(keyword, spec)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.lamp)
        layout.addWidget(self.display)

    def update_value(self, value) -> None:
        self.display.update_value(value)
        self.lamp.set_state(None if value is None else bool(self._ok(value)))


class ThermoArray(QWidget):
    """Labelled per-channel readouts fed by one thermocouple-array keyword
    (a list of degC, one element per DAQ channel).

    A channel reading null/NaN (open thermocouple, board offline) shows an
    em dash. Each channel carries its normal-operation baseline: a reading
    more than ``tolerance_C`` above it turns bold red, more than
    ``tolerance_C`` below bold blue (the tooltip shows the expected value).
    ``channels`` is ``(channel, label, tooltip, baseline_C)`` — channels
    left out (e.g. the rack board's permanently unconnected ch7) are simply
    not shown.
    """

    def __init__(
        self,
        keyword: str,
        channels: list[tuple[int, str, str, float]],
        tolerance_C: float = 3.0,
        columns: int = 2,
    ):
        super().__init__()
        self.keyword = keyword
        self.tolerance_C = tolerance_C
        self._cells: list[tuple[int, float, QLabel]] = []
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(18)
        for index, (channel, label, tooltip, baseline_C) in enumerate(channels):
            row, pair = divmod(index, columns)
            name = QLabel(label)
            name.setToolTip(
                f"{tooltip} — {keyword}[{channel}], normally {baseline_C:.1f} ±{tolerance_C:.0f} °C"
            )
            value = QLabel("—")
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            value.setToolTip(name.toolTip())
            grid.addWidget(name, row, pair * 2)
            grid.addWidget(value, row, pair * 2 + 1)
            self._cells.append((channel, baseline_C, value))

    def update_value(self, values) -> None:
        if not isinstance(values, (list, tuple)):
            values = ()
        for channel, baseline_C, cell in self._cells:
            value = values[channel] if channel < len(values) else None
            if value is None or (isinstance(value, float) and not math.isfinite(value)):
                cell.setText("—")
                cell.setStyleSheet("")
                continue
            cell.setText(f"{value:.2f} °C")
            if value > baseline_C + self.tolerance_C:
                cell.setStyleSheet("color: #e05252; font-weight: bold;")
            elif value < baseline_C - self.tolerance_C:
                cell.setStyleSheet("color: #5b9bd5; font-weight: bold;")
            else:
                cell.setStyleSheet("")


class KeywordSpinBox(QWidget):
    """Spin box + apply button for a writable numeric keyword.

    The spin box shows the live value until the user edits it; Enter (or
    focus-out) submits the write through the submit callback. A just-typed
    value is held for a grace period instead of being snapped back by the
    next poll, so a slow (or refused) write doesn't look like the box ate
    the input — after the grace the poll value wins again.

    ``live`` additionally submits arrow/wheel steps after a short pause
    (issue #41: the IM bias felt dead — stepping the arrows changed the
    display but nothing was written until focus left the box). Typed
    edits still wait for Enter/focus-out: keyboard tracking is off in
    this mode, so keystrokes never emit ``valueChanged``, and rapid
    arrow clicks coalesce into one write.

    ``readback`` splits setpoint from measurement instead: the (narrower)
    box keeps what the operator — or a commissioned default — put there,
    and the live value is shown as text beside it. Used where the box is a
    value to apply rather than a value to watch (the Pritel currents read
    back 0 until emission is on, so a poll-tracking box would erase the
    setpoint the operator is about to send).
    """

    #: seconds a submitted value is protected from poll snap-back
    PENDING_GRACE_S = 10.0
    #: max spin width (px) when a readback label shares the row
    READBACK_SPIN_WIDTH = 120
    #: ms of arrow-click inactivity before a live box submits
    LIVE_APPLY_DELAY_MS = 400

    def __init__(
        self,
        keyword: str,
        spec: dict,
        submit: Callable[[str, object], None],
        readback: bool = False,
        live: bool = False,
    ):
        super().__init__()
        self.keyword = keyword
        self.units = spec.get("units", "")
        self._submit = submit
        self._editing = False
        self._pending_until = 0.0
        self._debounce: QTimer | None = None
        if live:
            self._debounce = QTimer(self)
            self._debounce.setSingleShot(True)
            self._debounce.setInterval(self.LIVE_APPLY_DELAY_MS)
            self._debounce.timeout.connect(self._apply)

        self.spin = SelectAllSpinBox()
        if live:
            # keystrokes must not emit valueChanged (each would submit a
            # half-typed number); arrows/wheel/PageUp still do
            self.spin.setKeyboardTracking(False)
        self.spin.setDecimals(3)
        self.spin.setRange(
            spec.get("min") if spec.get("min") is not None else -1e9,
            spec.get("max") if spec.get("max") is not None else 1e9,
        )
        if spec.get("step") is not None:  # arrow/wheel increment
            self.spin.setSingleStep(spec["step"])
        # Qt sizes a spin box for its range's widest text — keywords with
        # no schema limits get ±1e9 and a comically wide box that used to
        # set the whole window's minimum width. Real values fit in this.
        self.spin.setMaximumWidth(130)
        if self.units:
            self.spin.setSuffix(f" {self.units}")
        if spec.get("help"):
            self.spin.setToolTip(spec["help"])
        self.spin.editingFinished.connect(self._apply)
        # mark "editing" as soon as the user changes the value by any means
        self.spin.valueChanged.connect(self._on_user_change)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.spin)

        self.readback: QLabel | None = None
        if readback:
            self.spin.setMaximumWidth(self.READBACK_SPIN_WIDTH)
            self.readback = QLabel("—")
            self.readback.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.readback.setToolTip(f"value measured by the instrument ({keyword})")
            layout.addWidget(self.readback)

    def _on_user_change(self, _value) -> None:
        if self.spin.hasFocus():
            self._editing = True
            if self._debounce is not None:
                self._debounce.start()  # restart on every step: clicks coalesce

    def _apply(self) -> None:
        if self._editing:
            self._editing = False
            if self._debounce is not None:
                self._debounce.stop()  # editingFinished beat the timer
            self._pending_until = time.monotonic() + self.PENDING_GRACE_S
            self._submit(self.keyword, self.spin.value())

    def write_rejected(self) -> None:
        """The submitted write failed: stop protecting it so the next
        poll restores the instrument's real value."""
        self._pending_until = 0.0

    def update_value(self, value) -> None:
        if self.readback is not None:
            # setpoint/measurement split: the poll only feeds the label,
            # never the box
            self.readback.setText(format_value(value, self.units))
            return
        if value is None:  # unknown (e.g. VOA not homed): keep what's shown
            return
        if self._editing or self.spin.hasFocus():
            return
        value = float(value)
        if value != self.spin.value() and time.monotonic() < self._pending_until:
            return  # a submitted write hasn't reached the poll cache yet
        self.spin.blockSignals(True)
        self.spin.setValue(value)
        self.spin.blockSignals(False)


class OnOffButton(QWidget):
    """Toggle button + lamp for a boolean keyword.

    ``confirm`` adds an are-you-sure dialog — used for anything that
    switches optical power (EDFAs, Pritel pump, RF chain).
    ``confirm_message`` customizes its text: a callable taking the
    target state, evaluated at click time so it can quote live values
    (the Pritel dialog names the current the bring-up will ramp to).
    """

    def __init__(
        self,
        keyword: str,
        spec: dict,
        submit: Callable[[str, object], None],
        confirm: bool = False,
        label: str = "",
        confirm_message: Callable[[bool], str] | None = None,
    ):
        super().__init__()
        self.keyword = keyword
        self._submit = submit
        self._confirm = confirm
        self._confirm_message = confirm_message
        self._state: bool | None = None

        self.lamp = StatusLamp(label or keyword)
        self.button = QPushButton("—")
        if spec.get("help"):
            self.button.setToolTip(spec["help"])
        self.button.clicked.connect(self._toggle)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.lamp)
        layout.addWidget(self.button)

    def _toggle(self) -> None:
        target = not bool(self._state)
        if self._confirm:
            verb = "turn ON" if target else "turn OFF"
            message = (
                self._confirm_message(target)
                if self._confirm_message is not None
                else f"Really {verb} {self.keyword}?"
            )
            answer = QMessageBox.question(
                self,
                "Confirm",
                message,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._submit(self.keyword, "1" if target else "0")

    def update_value(self, value) -> None:
        self._state = bool(value)
        self.lamp.set_state(self._state)
        self.button.setText("Turn OFF" if self._state else "Turn ON")
