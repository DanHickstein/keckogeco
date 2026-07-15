"""Dark theme for the engineering GUI.

One place for the palette so panels, lamps, and plots stay consistent.
Applied app-wide from ``gui/app.py`` (Fusion style + a QSS sheet), so the
per-widget code never hardcodes chrome colors — only semantic ones
(state banner, lamps) which also live here.
"""

from __future__ import annotations

__all__ = ["ACCENT", "PLOT_BG", "STATE_COLORS", "apply_dark_theme"]

ACCENT = "#4fd1c5"  # teal — titles, highlights, plot traces
PLOT_BG = "#0f131a"

#: comb-state banner colors (FAULT is shown as ENGINEERING MODE)
STATE_COLORS = {
    "FULL COMB": "#1e8e3e",
    "STANDBY": "#c78a00",
    "OFF": "#4a5461",
    "FAULT": "#7c5cd6",  # mixed config -> engineering mode, not an alarm
    "UNKNOWN": "#5a6472",
    "TRANSITIONING": "#2f6fd0",
}

_QSS = f"""
QWidget {{
    background-color: #12161d;
    color: #d7dee8;
    font-size: 12px;
}}
QLabel, QCheckBox, QRadioButton {{ background: transparent; }}
QMainWindow {{ background-color: #0e1116; }}
QGroupBox {{
    border: 1px solid #29323f;
    border-radius: 8px;
    margin-top: 12px;
    padding: 6px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
    color: {ACCENT};
    font-weight: bold;
}}
QPushButton {{
    background-color: #222a37;
    border: 1px solid #354052;
    border-radius: 4px;
    padding: 4px 10px;
}}
QPushButton:hover {{ background-color: #2b3545; border-color: {ACCENT}; }}
QPushButton:pressed {{ background-color: #1a212c; }}
QDoubleSpinBox, QSpinBox, QLineEdit {{
    background-color: {PLOT_BG};
    border: 1px solid #2b3442;
    border-radius: 4px;
    padding: 2px 4px;
    selection-background-color: {ACCENT};
    selection-color: #0e1116;
}}
QTabWidget::pane {{ border: 1px solid #29323f; border-radius: 6px; }}
QTabBar::tab {{
    background: #161b24;
    border: 1px solid #29323f;
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    padding: 6px 16px;
    margin-right: 2px;
    color: #8b96a5;
}}
QTabBar::tab:selected {{ background: #1d2430; color: {ACCENT}; }}
QTabBar::tab:hover {{ color: #d7dee8; }}
QStatusBar {{ background-color: #0e1116; color: #8b96a5; }}
QToolTip {{
    background-color: #1d2430;
    color: #d7dee8;
    border: 1px solid {ACCENT};
}}
QMessageBox {{ background-color: #171c25; }}
"""


def apply_dark_theme(app) -> None:
    """Style a QApplication: Fusion base + the dark QSS sheet."""
    app.setStyle("Fusion")
    app.setStyleSheet(_QSS)
