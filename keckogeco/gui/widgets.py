"""Shared widgets for the engineering GUI.

Every control is keyed by its KTL keyword; units, limits, and writability
come from the server's ``/schema`` endpoint, so the GUI never hardcodes a
range that the schema (and therefore Keck) doesn't agree with.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QWidget,
)

__all__ = ["KeywordDisplay", "KeywordSpinBox", "OnOffButton", "StatusLamp"]

_LAMP_COLORS = {
    True: "#2e7d32",  # green
    False: "#616161",  # grey
    "fault": "#c62828",  # red
    None: "#9e9e9e",
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
            f"background-color: {color}; border-radius: 8px; border: 1px solid #333;"
        )
        state_name = {True: "ON", False: "OFF", None: "?"}.get(state, str(state))
        self.setToolTip(f"{self._label}: {state_name}")


class KeywordDisplay(QLabel):
    """Read-only value display for one keyword."""

    def __init__(self, keyword: str, spec: dict):
        super().__init__("—")
        self.keyword = keyword
        self.units = spec.get("units", "")
        self.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.setToolTip(spec.get("help") or keyword)

    def update_value(self, value) -> None:
        if value is None:  # server reports unknown (e.g. VOA not homed) as null
            self.setText("—")
            return
        if isinstance(value, bool):
            text = "ON" if value else "OFF"
        elif isinstance(value, float):
            text = f"{value:.3f}"
        else:
            text = str(value)
        self.setText(f"{text} {self.units}".strip())


class KeywordSpinBox(QWidget):
    """Spin box + apply button for a writable numeric keyword.

    The spin box shows the live value until the user edits it; Apply (or
    Enter) submits the write through the submit callback.
    """

    def __init__(self, keyword: str, spec: dict, submit: Callable[[str, object], None]):
        super().__init__()
        self.keyword = keyword
        self._submit = submit
        self._editing = False

        self.spin = QDoubleSpinBox()
        self.spin.setDecimals(3)
        self.spin.setRange(
            spec.get("min") if spec.get("min") is not None else -1e9,
            spec.get("max") if spec.get("max") is not None else 1e9,
        )
        if spec.get("units"):
            self.spin.setSuffix(f" {spec['units']}")
        self.spin.setToolTip(spec.get("help") or keyword)
        self.spin.editingFinished.connect(self._apply)
        # mark "editing" as soon as the user changes the value by any means
        self.spin.valueChanged.connect(self._on_user_change)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.spin)

    def _on_user_change(self, _value) -> None:
        if self.spin.hasFocus():
            self._editing = True

    def _apply(self) -> None:
        if self._editing:
            self._editing = False
            self._submit(self.keyword, self.spin.value())

    def update_value(self, value) -> None:
        if value is None:  # unknown (e.g. VOA not homed): keep what's shown
            return
        if not self._editing and not self.spin.hasFocus():
            self.spin.blockSignals(True)
            self.spin.setValue(float(value))
            self.spin.blockSignals(False)


class OnOffButton(QWidget):
    """Toggle button + lamp for a boolean keyword.

    ``confirm`` adds an are-you-sure dialog — used for anything that
    switches optical power (EDFAs, Pritel pump, RF chain).
    """

    def __init__(
        self,
        keyword: str,
        spec: dict,
        submit: Callable[[str, object], None],
        confirm: bool = False,
        label: str = "",
    ):
        super().__init__()
        self.keyword = keyword
        self._submit = submit
        self._confirm = confirm
        self._state: bool | None = None

        self.lamp = StatusLamp(label or keyword)
        self.button = QPushButton("—")
        self.button.setToolTip(spec.get("help") or keyword)
        self.button.clicked.connect(self._toggle)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.lamp)
        layout.addWidget(self.button)

    def _toggle(self) -> None:
        target = not bool(self._state)
        if self._confirm:
            verb = "turn ON" if target else "turn OFF"
            answer = QMessageBox.question(
                self,
                "Confirm",
                f"Really {verb} {self.keyword}?",
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
