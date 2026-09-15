"""Standalone viewer for the Yokogawa AQ63xx OSA.

Run with ``python -m keckogeco.gui.yokogawa_app`` (or open this file and
press Run in VSCode). Unlike the main GUI this talks to the instrument
directly — the Yokogawa hangs off the second GPIB-USB adapter and is not
(yet) a server device, so owning the port here doesn't conflict with the
server. Auto-detection deliberately skips board GPIB0: that bus belongs
to the server's Agilent 86142B.

``--sim`` runs against the driver's canned responses for an offline
layout check; ``--address GPIB1::1::INSTR`` skips auto-detection.
"""

from __future__ import annotations

import argparse
import contextlib
import queue
import sys
import threading
import time
from pathlib import Path

if __package__ in (None, ""):  # run as a bare file (VSCode Run button)
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

__all__ = ["main"]

POLL_MS_DEFAULT = 1000
#: default plot Y limits (dBm); fixed, not autoscaled, because the OSA
#: reports unmeasured trace points as -210 dBm
YLIM_DEFAULT = (-70.0, 0.0)


def find_yokogawa(exclude_board0: bool = True) -> str | None:
    """Scan GPIB VISA resources (not board 0) for a YOKOGAWA *IDN? reply."""
    import pyvisa

    rm = pyvisa.ResourceManager()
    for address in rm.list_resources("GPIB?*::INSTR"):
        if exclude_board0 and address.upper().startswith("GPIB0"):
            continue  # the server's Agilent bus — never probe it from here
        try:
            resource = rm.open_resource(address)
            try:
                resource.timeout = 2000
                resource.write("CFORM1")  # wake a unit stuck in AQ6317 mode
                idn = resource.query("*IDN?")
            finally:
                resource.close()
        except Exception:  # noqa: BLE001 - a silent address is just not ours
            continue
        if "YOKOGAWA" in idn.upper():
            return address
    return None


def _build_osa(address: str, sim: bool):
    from keckogeco.drivers.transports import SimTransport, VisaTransport
    from keckogeco.drivers.yokogawa_osa import YokogawaOSA

    if sim:
        transport = SimTransport(YokogawaOSA.sim_responses(), address="SIM::yokogawa")
    else:
        transport = VisaTransport(address, **YokogawaOSA.TRANSPORT_DEFAULTS)
    return YokogawaOSA(transport, "yokogawa_osa")


def _make_worker_class():
    """Define the QThread worker lazily so importing this module never
    requires PyQt6 (keeps the module importable in headless CI)."""
    from PyQt6.QtCore import QThread, pyqtSignal

    class OsaWorker(QThread):
        """Owns the instrument: all I/O happens on this thread, the GUI
        thread only enqueues callables and consumes signals."""

        sig_connected = pyqtSignal(str, str)  # idn, address
        sig_disconnected = pyqtSignal()
        sig_trace = pyqtSignal(object, object)  # wavelength_nm, power_dBm
        sig_status = pyqtSignal(dict)
        sig_error = pyqtSignal(str)

        def __init__(self, sim: bool, poll_ms: int):
            super().__init__()
            self._sim = sim
            self._poll_s = max(poll_ms, 200) / 1000
            self._commands: queue.Queue = queue.Queue()
            self._stop_event = threading.Event()
            self._osa = None

        # ------------------------------------------------- GUI-thread API

        def submit(self, fn) -> None:
            self._commands.put(fn)

        def connect_osa(self, address: str) -> None:
            self.submit(lambda: self._do_connect(address))

        def disconnect_osa(self) -> None:
            self.submit(self._do_disconnect)

        def apply(self, setter, label: str) -> None:
            """Run a driver call, then refresh status so the GUI shows what
            the instrument actually accepted (it coerces bad values)."""

            def _run():
                try:
                    setter(self._require())
                except Exception as exc:  # noqa: BLE001 - show, keep polling
                    self.sig_error.emit(f"{label}: {exc}")
                self._poll_status()

            self.submit(_run)

        def shutdown(self) -> None:
            self._stop_event.set()
            self._commands.put(None)

        # ---------------------------------------------------- worker side

        def _require(self):
            if self._osa is None:
                raise RuntimeError("not connected")
            return self._osa

        def _do_connect(self, address: str) -> None:
            self._do_disconnect()
            try:
                if not address and not self._sim:
                    address = find_yokogawa()
                    if not address:
                        self.sig_error.emit(
                            "no YOKOGAWA found on GPIB boards 1+ — enter the "
                            "VISA address (e.g. GPIB1::1::INSTR) and retry"
                        )
                        return
                osa = _build_osa(address, self._sim)
                osa.connect()
            except Exception as exc:  # noqa: BLE001 - present any failure
                self.sig_error.emit(f"connect failed: {exc}")
                return
            self._osa = osa
            self.sig_connected.emit(osa.identity, osa.transport.address)
            self._poll_status()

        def _do_disconnect(self) -> None:
            if self._osa is not None:
                with contextlib.suppress(Exception):  # closing must never block exit
                    self._osa.close()
                self._osa = None
                self.sig_disconnected.emit()

        def _poll_status(self) -> None:
            try:
                self.sig_status.emit(self._require().status())
            except Exception as exc:  # noqa: BLE001
                self.sig_error.emit(str(exc))

        def _poll_trace(self) -> None:
            try:
                wavelength, power = self._require().get_spectrum("A")
            except Exception as exc:  # noqa: BLE001
                self.sig_error.emit(str(exc))
                return
            self.sig_trace.emit(wavelength, power)

        def run(self) -> None:
            next_poll = 0.0
            while not self._stop_event.is_set():
                try:
                    fn = self._commands.get(timeout=0.1)
                except queue.Empty:
                    fn = None
                if self._stop_event.is_set():
                    break
                if fn is not None:
                    try:
                        fn()
                    except Exception as exc:  # noqa: BLE001 - worker must survive
                        self.sig_error.emit(str(exc))
                    continue
                if self._osa is not None and time.monotonic() >= next_poll:
                    self._poll_trace()
                    next_poll = time.monotonic() + self._poll_s
            self._do_disconnect()

    return OsaWorker


def _make_window_class():
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import (
        QCheckBox,
        QComboBox,
        QDoubleSpinBox,
        QFileDialog,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMainWindow,
        QPushButton,
        QVBoxLayout,
        QWidget,
    )

    from keckogeco.drivers.yokogawa_osa import YokogawaOSA
    from keckogeco.gui import prefs, spectra
    from keckogeco.gui.theme import ACCENT, MUTED, PLOT_BG

    class YokogawaWindow(QMainWindow):
        def __init__(self, worker, address: str):
            super().__init__()
            self.setWindowTitle("Yokogawa AQ63xx OSA")
            self._worker = worker
            self._last_trace: tuple | None = None
            self._last_status: dict = {}
            self._ref_curve = None
            self._ref_csv: str | None = None

            central = QWidget()
            layout = QVBoxLayout(central)

            # --- connection row
            row = QHBoxLayout()
            row.addWidget(QLabel("VISA address:"))
            self._addr = QLineEdit(address)
            self._addr.setPlaceholderText("blank = auto-detect on GPIB1+")
            self._addr.setMinimumWidth(180)
            row.addWidget(self._addr)
            self._connect_btn = QPushButton("Connect")
            self._connect_btn.clicked.connect(self._toggle_connect)
            row.addWidget(self._connect_btn)
            self._idn_label = QLabel("not connected")
            self._idn_label.setStyleSheet(f"color: {MUTED};")
            row.addWidget(self._idn_label, 1)
            layout.addLayout(row)

            # --- spectrum plot
            try:
                import pyqtgraph as pg

                plot = pg.PlotWidget()
                plot.setBackground(PLOT_BG)
                plot.showGrid(x=True, y=True, alpha=0.25)
                # plain-text labels: pyqtgraph `units=` would SI-scale
                # 1550 nm into "1.55 knm" on the axis
                plot.setLabel("bottom", "wavelength (nm)")
                plot.setLabel("left", "power (dBm)")
                self._curve = plot.plot(pen=pg.mkPen(ACCENT, width=1))
                # fixed Y range (X keeps autoscaling): the OSA fills
                # unmeasured points with -210 dBm, which would otherwise
                # drag the autoscale down to the floor
                plot.setYRange(YLIM_DEFAULT[0], YLIM_DEFAULT[1], padding=0)
                self._plot = plot
                layout.addWidget(plot, 1)
            except ImportError:
                placeholder = QLabel("(pyqtgraph not installed; spectrum hidden)")
                placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
                layout.addWidget(placeholder, 1)
                self._curve = None
                self._plot = None

            # --- acquisition settings
            settings = QGroupBox("Acquisition")
            grid = QHBoxLayout(settings)

            def spin(label, lo, hi, decimals, step, suffix):
                grid.addWidget(QLabel(label))
                box = QDoubleSpinBox()
                box.setRange(lo, hi)
                box.setDecimals(decimals)
                box.setSingleStep(step)
                box.setSuffix(suffix)
                box.setKeyboardTracking(False)
                grid.addWidget(box)
                return box

            # wavelength limits span the whole AQ63xx family (AQ6373 goes
            # down to 350 nm, the AQ6376 up to 3400 nm)
            self._start = spin("Start", 350.0, 3400.0, 2, 1.0, " nm")
            self._stop = spin("Stop", 350.0, 3400.0, 2, 1.0, " nm")
            grid.addWidget(QLabel("Res"))
            self._res = QComboBox()
            self._res.setEditable(True)
            for value in YokogawaOSA.RESOLUTIONS_NM:
                self._res.addItem(f"{value:g}")
            grid.addWidget(self._res)
            grid.addWidget(QLabel("nm"))
            grid.addWidget(QLabel("Sens"))
            self._sens = QComboBox()
            self._sens.addItems(YokogawaOSA.SENSITIVITIES)
            grid.addWidget(self._sens)
            self._rlev = spin("Ref level", -90.0, 30.0, 1, 1.0, " dBm")
            grid.addStretch(1)
            layout.addWidget(settings)

            self._start.editingFinished.connect(
                lambda: self._apply(
                    "start", lambda osa: setattr(osa, "wl_start_nm", self._start.value())
                )
            )
            self._stop.editingFinished.connect(
                lambda: self._apply(
                    "stop", lambda osa: setattr(osa, "wl_stop_nm", self._stop.value())
                )
            )
            self._res.activated.connect(
                lambda _i: self._apply(
                    "resolution",
                    lambda osa: setattr(osa, "resolution_nm", float(self._res.currentText())),
                )
            )
            self._sens.activated.connect(
                lambda _i: self._apply(
                    "sensitivity", lambda osa: setattr(osa, "sensitivity", self._sens.currentText())
                )
            )
            self._rlev.editingFinished.connect(
                lambda: self._apply(
                    "ref level", lambda osa: setattr(osa, "reference_level_dBm", self._rlev.value())
                )
            )

            # --- sweep + save row
            row = QHBoxLayout()
            for label, mode in (("Single", "SINGLE"), ("Repeat", "REPEAT")):
                button = QPushButton(label)
                button.clicked.connect(
                    lambda _c, m=mode: self._apply(f"sweep {m}", lambda osa: osa.sweep(m))
                )
                row.addWidget(button)
            stop_btn = QPushButton("Stop")
            stop_btn.clicked.connect(lambda: self._apply("stop sweep", lambda osa: osa.abort()))
            row.addWidget(stop_btn)
            self._mode_label = QLabel("")
            self._mode_label.setStyleSheet(f"color: {MUTED};")
            row.addWidget(self._mode_label)
            row.addStretch(1)

            def ylim_spin(label, value):
                row.addWidget(QLabel(label))
                box = QDoubleSpinBox()
                box.setRange(-210.0, 30.0)
                box.setDecimals(1)
                box.setSingleStep(5.0)
                box.setSuffix(" dBm")
                box.setKeyboardTracking(False)
                box.setValue(value)
                box.valueChanged.connect(self._update_ylim)
                row.addWidget(box)
                return box

            self._ymin = ylim_spin("Y min", YLIM_DEFAULT[0])
            self._ymax = ylim_spin("Y max", YLIM_DEFAULT[1])
            save_btn = QPushButton("Save spectrum…")
            save_btn.clicked.connect(self._save)
            row.addWidget(save_btn)
            ref_btn = QPushButton("Load reference…")
            ref_btn.clicked.connect(self._load_reference)
            row.addWidget(ref_btn)
            self._ref_check = QCheckBox("Show ref")
            self._ref_check.setChecked(True)
            self._ref_check.toggled.connect(self._toggle_reference)
            row.addWidget(self._ref_check)
            layout.addLayout(row)

            self.setCentralWidget(central)
            self._restore_reference()
            self.statusBar().showMessage("ready")

            worker.sig_connected.connect(self._on_connected)
            worker.sig_disconnected.connect(self._on_disconnected)
            worker.sig_trace.connect(self._on_trace)
            worker.sig_status.connect(self._on_status)
            worker.sig_error.connect(lambda msg: self.statusBar().showMessage(msg, 10000))

        # ---------------------------------------------------------- slots

        def _toggle_connect(self) -> None:
            if self._connect_btn.text() == "Connect":
                self.statusBar().showMessage("connecting…")
                self._worker.connect_osa(self._addr.text().strip())
            else:
                self._worker.disconnect_osa()

        def _apply(self, label: str, setter) -> None:
            self._worker.apply(setter, label)

        def _update_ylim(self) -> None:
            if self._plot is None:
                return
            ymin, ymax = self._ymin.value(), self._ymax.value()
            if ymin >= ymax:
                self.statusBar().showMessage("Y min must be below Y max", 5000)
                return
            self._plot.setYRange(ymin, ymax, padding=0)

        def _on_connected(self, idn: str, address: str) -> None:
            self._connect_btn.setText("Disconnect")
            self._addr.setText(address)
            self._idn_label.setText(idn)
            self._idn_label.setStyleSheet(f"color: {ACCENT};")
            self.statusBar().showMessage(f"connected: {address}", 5000)

        def _on_disconnected(self) -> None:
            self._connect_btn.setText("Connect")
            self._idn_label.setText("not connected")
            self._idn_label.setStyleSheet(f"color: {MUTED};")
            self.statusBar().showMessage("disconnected", 5000)

        def _on_trace(self, wavelength, power) -> None:
            self._last_trace = (wavelength, power)
            if self._curve is not None:
                self._curve.setData(wavelength, power)

        def _on_status(self, status: dict) -> None:
            self._last_status = status
            for box, key in (
                (self._start, "wl_start_nm"),
                (self._stop, "wl_stop_nm"),
                (self._rlev, "reference_level_dBm"),
            ):
                if not box.hasFocus():
                    box.blockSignals(True)
                    box.setValue(float(status[key]))
                    box.blockSignals(False)
            if not self._res.hasFocus():
                self._res.setCurrentText(f"{status['resolution_nm']:g}")
            if not self._sens.hasFocus():
                self._sens.setCurrentText(status["sensitivity"])
            self._mode_label.setText(f"sweep: {status['sweep_mode']}")

        def _spectra_dir(self) -> Path:
            directory = prefs.GUI_CONFIG_PATH.parent.parent / "spectra"
            directory.mkdir(parents=True, exist_ok=True)
            return directory

        def _save(self) -> None:
            if not self._last_trace:
                self.statusBar().showMessage("no spectrum to save yet", 5000)
                return
            default = self._spectra_dir() / time.strftime("yokogawa_%Y-%m-%d_%H%M%S.csv")
            path, _filter = QFileDialog.getSaveFileName(
                self, "Save spectrum", str(default), "CSV files (*.csv)"
            )
            if not path:
                return
            wavelength, power = self._last_trace
            metadata = {
                "instrument": self._idn_label.text(),
                **self._last_status,
                "points": len(wavelength),
            }
            try:
                spectra.save_spectrum_csv(path, list(wavelength), list(power), metadata)
            except (OSError, ValueError) as exc:
                self.statusBar().showMessage(f"SAVE FAILED: {exc}", 10000)
                return
            self.statusBar().showMessage(f"saved {path}", 8000)

        # ------------------------------------------- reference spectrum

        def _set_reference(self, x: list, y: list) -> None:
            if self._plot is None:
                return
            if self._ref_curve is None:
                import pyqtgraph as pg

                # dashed purple, same style as the main GUI's OSA
                # reference overlay; z < 0 keeps it behind the live trace
                pen = pg.mkPen("#b085f5", width=1, style=Qt.PenStyle.DashLine)
                self._ref_curve = self._plot.plot(pen=pen, name="reference")
                self._ref_curve.setZValue(-1)
            self._ref_curve.setData(x, y)
            self._ref_curve.setVisible(self._ref_check.isChecked())

        def _load_reference(self) -> None:
            path, _filter = QFileDialog.getOpenFileName(
                self, "Load reference spectrum", str(self._spectra_dir()), "CSV files (*.csv)"
            )
            if not path:
                return
            try:
                x, y, _metadata = spectra.load_spectrum_csv(path)
            except (OSError, ValueError) as exc:
                self.statusBar().showMessage(f"LOAD FAILED: {exc}", 10000)
                return
            self._ref_csv = Path(path).as_posix()
            self._ref_check.setChecked(True)  # loading implies showing
            self._set_reference(x, y)
            self._save_ref_prefs()
            self.statusBar().showMessage(f"reference spectrum: {path}", 8000)

        def _toggle_reference(self, checked: bool) -> None:
            if self._ref_curve is not None:
                self._ref_curve.setVisible(checked)
            if self._ref_csv:
                self._save_ref_prefs()

        def _save_ref_prefs(self) -> None:
            prefs.save_section(
                "yokogawa_reference",
                {"csv": self._ref_csv, "show": self._ref_check.isChecked()},
            )

        def _restore_reference(self) -> None:
            """Re-display the saved reference spectrum, if one was ever loaded."""
            saved = prefs.load_section("yokogawa_reference")
            csv = saved.get("csv")
            if not csv:
                return
            try:
                x, y, _metadata = spectra.load_spectrum_csv(csv)
            except (OSError, ValueError) as exc:
                self.statusBar().showMessage(f"reference spectrum not restored: {exc}", 10000)
                return
            self._ref_csv = csv
            self._ref_check.setChecked(bool(saved.get("show", True)))
            self._set_reference(x, y)

        def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
            self._worker.shutdown()
            self._worker.wait(3000)
            super().closeEvent(event)

    return YokogawaWindow


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Standalone Yokogawa AQ63xx OSA viewer")
    parser.add_argument(
        "--address",
        default="",
        help="VISA resource (e.g. GPIB1::1::INSTR); blank auto-detects on GPIB boards 1+",
    )
    parser.add_argument("--sim", action="store_true", help="run against canned responses")
    parser.add_argument("--poll", type=int, default=POLL_MS_DEFAULT, help="trace poll period in ms")
    args = parser.parse_args(argv)

    from PyQt6.QtWidgets import QApplication

    from keckogeco.gui.theme import apply_dark_theme

    app = QApplication(sys.argv[:1])
    apply_dark_theme(app)
    worker = _make_worker_class()(sim=args.sim, poll_ms=args.poll)
    window = _make_window_class()(worker, args.address)
    worker.start()
    worker.connect_osa(args.address)  # auto-connect so Run-button launch just works
    window.resize(900, 560)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
