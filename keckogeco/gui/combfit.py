"""Fit a saved OSA comb spectrum to a one-IM + one-PM model.

Run with ``python -m keckogeco.gui.combfit [spectrum.csv]``, or open this
file and press Run in VSCode. Works entirely offline on the CSVs written
by the main GUI's OSA "Save" button (``keckogeco/gui/spectra.py`` format);
no server needed.

Model
-----
The rack has three phase modulators, but their depths are not separable
from a single envelope, so the comb is modelled as one Mach-Zehnder
intensity modulator followed by one lumped phase modulator, both driven
at the rep rate ``Ω``::

    E(t) = cos( [theta_b + m·cos(Ωt + phi)] / 2 ) · exp( i·beta·cos(Ωt) )

with ``beta`` the total PM depth (rad), ``m = π·V_rf/Vπ`` the IM drive
depth (rad), ``theta_b`` the IM bias phase (90° = quadrature) and ``phi``
the RF phase of the IM drive relative to the PM drive (``phi > 0`` = IM
leads).  ``phi`` is what makes the envelope asymmetric, so an asymmetric
spectrum pins its sign — but mirroring the spectrum (or flipping the sign
of both drives) maps ``phi → −phi``, so the sign is only meaningful
relative to the frequency axis orientation used here (increasing ν).

Comb-line powers are the squared Fourier coefficients of ``E`` over one
RF period, computed by FFT; the fit runs in dB space with the measured
noise floor as a soft lower clip so lines lost in the floor (e.g. near
IM nulls) still constrain the fit without dominating it.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

if __package__ in (None, ""):  # run as a bare file (VSCode Run button)
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
from scipy.optimize import least_squares
from scipy.signal import find_peaks

__all__ = [
    "CombFit",
    "CombLines",
    "extract_comb_lines",
    "fit_comb",
    "main",
    "model_line_powers_db",
]

#: ν [GHz] = _C_NM_GHZ / λ [nm]
_C_NM_GHZ = 299_792_458.0

_PARAM_NAMES = ("beta", "m", "theta_bias", "phase", "offset_db")


# --------------------------------------------------------------------------
# model


def _line_powers_db(beta, m, theta_bias, phase, n, nsamp=512):
    """dB power of comb line(s) ``n``; parameters may be (K,1) arrays."""
    wt = 2.0 * np.pi * np.arange(nsamp) / nsamp
    field = np.cos(0.5 * (theta_bias + m * np.cos(wt + phase))) * np.exp(1j * beta * np.cos(wt))
    coef = np.fft.fft(field) / nsamp
    power = np.abs(coef[..., np.mod(n, nsamp)]) ** 2
    return 10.0 * np.log10(np.maximum(power, 1e-30))


def model_line_powers_db(beta, m, theta_bias, phase, n, nsamp=512):
    """Model comb-line powers in dB (un-normalised; carrier is line 0).

    Parameters
    ----------
    beta : float
        Phase-modulation depth, rad.
    m : float
        IM drive depth, rad (``π·V_rf/Vπ``).
    theta_bias : float
        IM bias phase, rad (``π/2`` = quadrature).
    phase : float
        IM drive phase relative to the PM drive, rad.
    n : array_like of int
        Comb-line indices (0 = optical carrier, positive = higher ν).
    nsamp : int
        Samples per RF period for the FFT; must exceed ``2·max|n|``.
    """
    return _line_powers_db(float(beta), float(m), float(theta_bias), float(phase), np.asarray(n))


# --------------------------------------------------------------------------
# comb-line extraction


@dataclass
class CombLines:
    """Comb-line powers pulled off a measured spectrum, on a refined grid."""

    n: np.ndarray  #: integer line index (0 = anchor line, positive = higher ν)
    power_db: np.ndarray  #: measured line power, dBm
    wavelength_nm: np.ndarray  #: line position for plotting
    frep_ghz: float  #: refined line spacing
    nu0_ghz: float  #: optical frequency of line 0
    floor_db: float  #: estimated noise floor
    npeaks: int  #: peaks found by the detector (grid points may be more)


def extract_comb_lines(
    wavelength_nm, power_dbm, frep_ghz: float = 16.0, prominence_db: float = 5.0
) -> CombLines:
    """Find the comb lines in an OSA trace and index them on a uniform grid.

    Peaks establish the grid (spacing refined by a linear fit of peak
    frequency vs line number). Peaks that don't sit on the grid, and
    everything outside the contiguous highest-power block of lines, are
    rejected as noise spikes — an OSA floor at high sensitivity is spiky
    enough to clear a prominence threshold. Every grid position inside
    the block is then read back off the spectrum (max within ±0.3
    spacings), so lines buried in the floor become floor-valued points
    rather than gaps — those carry real information near IM nulls.
    """
    wl = np.asarray(wavelength_nm, dtype=float)
    db = np.asarray(power_dbm, dtype=float)
    if wl.size < 16:
        raise ValueError(f"spectrum has only {wl.size} points")
    nu = _C_NM_GHZ / wl
    order = np.argsort(nu)
    nu, db = nu[order], db[order]

    dnu = np.median(np.diff(nu))
    spacing_pts = max(int(round(0.6 * frep_ghz / dnu)), 1)
    peaks, _props = find_peaks(db, distance=spacing_pts, prominence=prominence_db)
    if peaks.size < 5:
        raise ValueError(
            f"only {peaks.size} comb lines found (need ≥ 5) — "
            "check the rep rate and prominence settings"
        )
    floor_db = float(np.percentile(db, 20))  # low percentile: the comb may cover half the span

    # sub-sample peak positions: the sample grid quantises ν by ~1.2 GHz,
    # far too coarse for the spacing fit. A parabola through the 3 points
    # around each peak is exact for a Gaussian lineshape (parabolic in dB).
    nu_pk = nu[peaks].copy()
    inner = (peaks > 0) & (peaks < nu.size - 1)
    left, mid, right = peaks[inner] - 1, peaks[inner], peaks[inner] + 1
    denom = (nu[left] - nu[mid]) * (nu[left] - nu[right]) * (nu[mid] - nu[right])
    a = (
        nu[right] * (db[mid] - db[left])
        + nu[mid] * (db[left] - db[right])
        + nu[left] * (db[right] - db[mid])
    ) / denom
    b = (
        nu[right] ** 2 * (db[left] - db[mid])
        + nu[mid] ** 2 * (db[right] - db[left])
        + nu[left] ** 2 * (db[mid] - db[right])
    ) / denom
    curved = a < -1e-12
    vertex = -b[curved] / (2.0 * a[curved])
    shift = np.clip(vertex - nu[mid[curved]], -1.5 * dnu, 1.5 * dnu)
    nu_pk[np.flatnonzero(inner)[curved]] = nu[mid[curved]] + shift
    # anchor on the strongest peak, then refine spacing with a linear fit
    db_pk = db[peaks]
    frep_fit = float(frep_ghz)
    nu0 = float(nu_pk[np.argmax(db_pk)])
    for _ in range(2):
        idx = np.round((nu_pk - nu0) / frep_fit)
        frep_fit, nu0 = (float(v) for v in np.polyfit(idx, nu_pk, 1))

    # reject peaks that don't sit on the grid (noise spikes land anywhere)
    idx = np.round((nu_pk - nu0) / frep_fit)
    on_grid = np.abs(nu_pk - (nu0 + idx * frep_fit)) < 0.25 * frep_fit
    if on_grid.sum() >= 5:
        nu_pk, db_pk, idx = nu_pk[on_grid], db_pk[on_grid], idx[on_grid]
        frep_fit, nu0 = (float(v) for v in np.polyfit(idx, nu_pk, 1))
        idx = np.round((nu_pk - nu0) / frep_fit)
    if not 0.5 * frep_ghz < frep_fit < 2.0 * frep_ghz:
        raise ValueError(
            f"fitted line spacing {frep_fit:.3f} GHz is far from the "
            f"expected {frep_ghz:.3f} GHz — wrong rep rate?"
        )

    # keep only strong peak blocks: on-grid noise stragglers sit just
    # above the prominence cut, real comb lines tens of dB higher. The
    # comb may have deep envelope nulls splitting it into several blocks
    # — the grid spans all strong blocks, and null lines come back as
    # floor-valued grid points below.
    order_idx = np.argsort(idx)
    idx_s, db_s = idx[order_idx].astype(int), db_pk[order_idx]
    blocks = np.split(np.arange(idx_s.size), np.flatnonzero(np.diff(idx_s) > 3) + 1)
    threshold = max(float(db_s.max()) - 20.0, floor_db + 8.0)
    kept = [block for block in blocks if db_s[block].max() >= threshold]
    grid = np.arange(idx_s[kept[0][0]], idx_s[kept[-1][-1]] + 1)
    npeaks = int(sum(len(block) for block in kept))
    power = np.full(grid.size, np.nan)
    for k, line in enumerate(grid):
        center = nu0 + line * frep_fit
        window = (nu > center - 0.3 * frep_fit) & (nu < center + 0.3 * frep_fit)
        if window.any():
            power[k] = db[window].max()
    keep = np.isfinite(power)
    grid, power = grid[keep], power[keep]
    return CombLines(
        n=grid,
        power_db=power,
        wavelength_nm=_C_NM_GHZ / (nu0 + grid * frep_fit),
        frep_ghz=frep_fit,
        nu0_ghz=nu0,
        floor_db=floor_db,
        npeaks=npeaks,
    )


# --------------------------------------------------------------------------
# fitting


@dataclass
class CombFit:
    """Best-fit IM + PM parameters for a set of measured comb lines."""

    beta: float  #: PM depth, rad
    m: float  #: IM drive depth, rad
    theta_bias: float  #: IM bias phase, rad
    phase: float  #: IM − PM RF phase, rad, wrapped to (−π, π]
    offset_db: float  #: power scale
    sigma: dict  #: 1σ uncertainties per parameter name (NaN if singular)
    n0: int  #: carrier position on the extraction grid
    model_db: np.ndarray  #: fitted line powers at ``CombLines.n``
    rms_db: float  #: rms residual over the fitted lines
    cost: float  #: least-squares cost (0.5·Σr²)
    twin_phase: float  #: best fit restarted at π − phase (see below), rad
    twin_rms_db: float  #: its rms — near ``rms_db`` means the twin is viable


def _residuals(params, n_rel, y, floor_db, nsamp=512):
    beta, m, theta_bias, phase, offset = params
    model = _line_powers_db(beta, m, theta_bias, phase, n_rel, nsamp) + offset
    return np.maximum(model, floor_db) - y


def _carrier_guess(lines: CombLines) -> int:
    """Grid index of the carrier ≈ linear-power centroid of the lines.

    A strong PM comb has its brightest lines at the *edges* (±beta), tens
    of lines from the carrier, so the strongest peak is a poor anchor;
    the centroid is off by at most a few lines (IM asymmetry)."""
    weight = 10 ** (lines.power_db / 10)
    return int(round(np.sum(weight * lines.n) / np.sum(weight)))


def _coarse_starts(lines: CombLines, center: int, nshift: int, top: int, nsamp: int) -> list[tuple]:
    """Rank a parameter grid by fit cost; return the ``top`` best starts."""
    y, floor = lines.power_db, lines.floor_db
    nspan = max(int(np.abs(lines.n - center).max()), 4)
    beta_g = np.linspace(0.3, max(8.0, 1.3 * nspan), 24)
    m_g = np.linspace(0.2, 3.0, 6)
    theta_g = np.linspace(0.15, np.pi - 0.15, 6)
    phase_g = np.linspace(-np.pi, np.pi, 12, endpoint=False)
    grids = np.meshgrid(beta_g, m_g, theta_g, phase_g, indexing="ij")
    flat = [g.ravel() for g in grids]

    shifts = center + np.arange(-nshift, nshift + 1)
    n_ext = np.arange(lines.n.min() - shifts.max(), lines.n.max() - shifts.min() + 1)
    strong = y > floor + 6.0
    if strong.sum() < 4:
        strong = np.ones_like(y, dtype=bool)

    best: list[tuple] = []  # (cost, beta, m, theta, phase, offset, n0)
    chunk = 2048
    for lo in range(0, flat[0].size, chunk):
        cols = [f[lo : lo + chunk, None] for f in flat]
        model_ext = _line_powers_db(*cols, n_ext, nsamp)
        for n0 in shifts:
            model = model_ext[:, lines.n - n0 - n_ext[0]]
            offset = np.median(y[strong] - model[:, strong], axis=1)
            clipped = np.maximum(model + offset[:, None], floor)
            cost = np.mean((clipped - y) ** 2, axis=1)
            k = int(np.argmin(cost))
            best.append(
                (float(cost[k]), *(float(c[k, 0]) for c in cols), float(offset[k]), int(n0))
            )
    best.sort(key=lambda item: item[0])
    return best[:top]


def _polish(start, n_rel, y, floor, nsamp, beta_max):
    bounds = ([0.0, 0.0, 0.0, -2 * np.pi, -300.0], [beta_max, 2 * np.pi, np.pi, 2 * np.pi, 300.0])
    return least_squares(
        _residuals,
        start,
        args=(n_rel, y, floor, nsamp),
        bounds=bounds,
        x_scale=[1, 1, 1, 1, 10.0],
    )


def _wrap(angle: float) -> float:
    return float((angle + np.pi) % (2 * np.pi) - np.pi)


def fit_comb(lines: CombLines, nshift: int = 3, nstarts: int = 8) -> CombFit:
    """Fit measured comb lines to the IM + PM model.

    A vectorised grid search over (beta, m, theta_bias, phase) and a
    ±``nshift``-line carrier position seeds ``nstarts`` local
    least-squares polishes; the best polish wins.

    The envelope asymmetry is ∝ sin(phase) at leading order, so ``phase``
    and ``π − phase`` fit almost equally well (they differ only through
    higher-order terms, typically ≲ 0.1 dB). The runner-up is polished
    too and reported as ``twin_phase``/``twin_rms_db``; if its rms is
    within the measurement systematics, only sin(phase) is trustworthy.
    """
    y, floor = lines.power_db, lines.floor_db
    center = _carrier_guess(lines)
    nspan = max(int(np.abs(lines.n - center).max()), 4)
    beta_max = max(25.0, 2.0 * nspan)
    # FFT length: comb indices (and beta-wide sidebands) must not alias
    nsamp = int(2 ** np.ceil(np.log2(max(512, 6 * nspan))))
    starts = _coarse_starts(lines, center, nshift, nstarts, nsamp)

    best_res, best_n0 = None, 0
    for _cost, beta, m, theta, phase, offset, n0 in starts:
        res = _polish([beta, m, theta, phase, offset], lines.n - n0, y, floor, nsamp, beta_max)
        if best_res is None or res.cost < best_res.cost:
            best_res, best_n0 = res, n0

    twin_start = list(best_res.x)
    twin_start[3] = _wrap(np.pi - best_res.x[3])
    twin_res = _polish(twin_start, lines.n - best_n0, y, floor, nsamp, beta_max)
    if twin_res.cost < best_res.cost:
        best_res, twin_res = twin_res, best_res

    beta, m, theta, phase, offset = best_res.x
    phase = _wrap(phase)
    twin_phase = _wrap(twin_res.x[3])
    twin_rms = float(np.sqrt(np.mean(twin_res.fun**2)))

    dof = max(y.size - len(_PARAM_NAMES), 1)
    variance = 2.0 * best_res.cost / dof
    jtj = best_res.jac.T @ best_res.jac
    try:
        diag = np.diag(np.linalg.inv(jtj)) * variance
        sigmas = np.sqrt(np.maximum(diag, 0.0))
    except np.linalg.LinAlgError:
        sigmas = np.full(len(_PARAM_NAMES), np.nan)

    model = _line_powers_db(beta, m, theta, phase, lines.n - best_n0, nsamp) + offset
    residual = np.maximum(model, floor) - y
    return CombFit(
        beta=float(beta),
        m=float(m),
        theta_bias=float(theta),
        phase=phase,
        offset_db=float(offset),
        sigma=dict(zip(_PARAM_NAMES, (float(s) for s in sigmas), strict=True)),
        n0=int(best_n0),
        model_db=model,
        rms_db=float(np.sqrt(np.mean(residual**2))),
        cost=float(best_res.cost),
        twin_phase=twin_phase,
        twin_rms_db=twin_rms,
    )


# --------------------------------------------------------------------------
# GUI


def _format_pm(value: float, sigma: float, decimals: int = 2, unit: str = "") -> str:
    text = f"{value:.{decimals}f}"
    if np.isfinite(sigma):
        text += f" ± {sigma:.{decimals}f}"
    return text + unit


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Fit a saved comb spectrum to an IM + PM model")
    parser.add_argument("csv", nargs="?", help="spectrum CSV to load on startup")
    args = parser.parse_args(argv)

    from PyQt6.QtWidgets import QApplication

    from keckogeco.gui.theme import apply_dark_theme

    app = QApplication(sys.argv[:1])
    apply_dark_theme(app)
    window = CombFitWindow()
    if args.csv:
        window.load_path(Path(args.csv))
    window.show()
    return app.exec()


class _FitWorker(QThread):
    """Runs ``fit_comb`` off the GUI thread."""

    finished_ok = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, lines: CombLines, parent=None):
        super().__init__(parent)
        self._lines = lines

    def run(self):
        try:
            self.finished_ok.emit(fit_comb(self._lines))
        except Exception as exc:  # noqa: BLE001 - report any failure to the GUI
            self.failed.emit(str(exc))


class CombFitWindow(QMainWindow):
    """Load a saved OSA spectrum, extract comb lines, fit the IM+PM model."""

    def __init__(self):
        super().__init__()
        from keckogeco.gui.theme import MUTED

        self.setWindowTitle("Comb fit — one IM + one PM")
        self.resize(1000, 620)
        self._lines: CombLines | None = None
        self._fit: CombFit | None = None
        self._worker: _FitWorker | None = None

        open_btn = QPushButton("Open CSV…")
        open_btn.clicked.connect(self._open_dialog)
        self._file_label = QLabel("no file loaded")
        self._file_label.setStyleSheet(f"color: {MUTED};")

        self.frep = QDoubleSpinBox()
        self.frep.setRange(0.5, 100.0)
        self.frep.setDecimals(3)
        self.frep.setValue(16.0)
        self.frep.setSuffix(" GHz")
        self.prominence = QDoubleSpinBox()
        self.prominence.setRange(1.0, 40.0)
        self.prominence.setValue(5.0)
        self.prominence.setSuffix(" dB")
        self.fit_btn = QPushButton("Fit")
        self.fit_btn.setEnabled(False)
        self.fit_btn.clicked.connect(self._start_fit)
        self._status = QLabel("")
        self._status.setStyleSheet(f"color: {MUTED};")

        bar = QHBoxLayout()
        bar.addWidget(open_btn)
        bar.addWidget(self._file_label, 1)
        bar.addWidget(QLabel("line spacing"))
        bar.addWidget(self.frep)
        bar.addWidget(QLabel("peak prominence"))
        bar.addWidget(self.prominence)
        bar.addWidget(self.fit_btn)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_plots())
        splitter.addWidget(self._build_results())
        splitter.setStretchFactor(0, 1)

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.addLayout(bar)
        layout.addWidget(splitter, 1)
        layout.addWidget(self._status)
        self.setCentralWidget(root)

    # ---- construction ----------------------------------------------------

    def _build_plots(self) -> QWidget:
        import pyqtgraph as pg

        from keckogeco.gui.theme import ACCENT, MUTED, PLOT_BG

        self._spec_plot = pg.PlotWidget()
        self._spec_plot.setBackground(PLOT_BG)
        self._spec_plot.setLabel("bottom", "wavelength (nm)")
        self._spec_plot.setLabel("left", "power (dBm)")
        self._spec_plot.addLegend(offset=(10, 10))
        self._spec_curve = self._spec_plot.plot(pen=pg.mkPen(ACCENT, width=1), name="measured")
        self._peak_dots = self._spec_plot.plot(
            pen=None,
            symbol="o",
            symbolSize=6,
            symbolPen=pg.mkPen(ACCENT),
            symbolBrush=None,
            name="comb lines",
        )
        self._fit_curve = self._spec_plot.plot(
            pen=pg.mkPen("#e8a33d", width=1),
            symbol="d",
            symbolSize=7,
            symbolPen=pg.mkPen("#e8a33d"),
            symbolBrush="#e8a33d",
            name="IM+PM fit",
        )

        self._res_plot = pg.PlotWidget()
        self._res_plot.setBackground(PLOT_BG)
        self._res_plot.setLabel("bottom", "wavelength (nm)")
        self._res_plot.setLabel("left", "fit − meas (dB)")
        self._res_plot.setMaximumHeight(140)
        self._res_plot.setXLink(self._spec_plot)
        self._res_plot.addLine(y=0, pen=pg.mkPen(MUTED, width=1))
        self._res_dots = self._res_plot.plot(
            pen=None, symbol="o", symbolSize=5, symbolPen=None, symbolBrush=MUTED
        )

        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._spec_plot, 1)
        layout.addWidget(self._res_plot)
        return box

    def _build_results(self) -> QWidget:
        from keckogeco.gui.theme import ACCENT, MUTED

        group = QGroupBox("Fit results")
        form = QFormLayout(group)

        self._phase_label = QLabel("—")
        self._phase_label.setStyleSheet(f"color: {ACCENT}; font-size: 20px; font-weight: 600;")
        form.addRow("IM−PM phase φ", self._phase_label)
        self._twin_label = QLabel("—")
        self._twin_label.setStyleSheet(f"color: {MUTED};")
        form.addRow("twin 180°−φ", self._twin_label)

        self._value_labels: dict[str, QLabel] = {}
        for key, caption in [
            ("beta", "PM depth β"),
            ("m", "IM depth m"),
            ("theta_bias", "IM bias θb"),
            ("offset_db", "power offset"),
            ("rms", "rms residual"),
            ("grid", "comb grid"),
        ]:
            label = QLabel("—")
            self._value_labels[key] = label
            form.addRow(caption, label)

        note = QLabel(
            "E(t) = cos(½[θb + m·cos(Ωt+φ)]) · e^{iβ·cos Ωt}\n\n"
            "φ > 0: IM drive leads the PM drive.  m = π·Vrf/Vπ; "
            "θb = 90° is quadrature.\n\n"
            "The envelope asymmetry pins sin φ; φ and 180°−φ differ "
            "only at higher order, so if the twin's rms is comparable, "
            "trust sin φ but not which of the two φ it is.\n\n"
            "Mirroring the spectrum (or flipping the sign of both "
            "drives) maps φ → −φ, so the sign is relative to the "
            "increasing-frequency axis."
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {MUTED};")
        form.addRow(note)

        panel = QWidget()
        panel.setMinimumWidth(300)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(group)
        layout.addStretch(1)
        return panel

    # ---- loading & extraction --------------------------------------------

    def _open_dialog(self):
        spectra_dir = Path(__file__).resolve().parents[2] / "spectra"
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "Open spectrum CSV",
            str(spectra_dir if spectra_dir.is_dir() else Path.home()),
            "Spectrum CSV (*.csv);;All files (*)",
        )
        if path:
            self.load_path(Path(path))

    def load_path(self, path: Path):
        """Load a spectrum CSV, extract its comb lines, and start a fit."""
        from keckogeco.gui.spectra import load_spectrum_csv

        try:
            x, y, _meta = load_spectrum_csv(path)
            self._wl, self._db = np.asarray(x, float), np.asarray(y, float)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Load failed", str(exc))
            return
        self._file_label.setText(path.name)
        self._file_label.setToolTip(str(path))
        self._spec_curve.setData(self._wl, self._db)
        self._extract()
        if self._lines is not None:
            self._start_fit()

    def _extract(self):
        self._lines = None
        self._fit = None
        self._fit_curve.setData([], [])
        self._res_dots.setData([], [])
        try:
            self._lines = extract_comb_lines(
                self._wl, self._db, self.frep.value(), self.prominence.value()
            )
        except ValueError as exc:
            self._peak_dots.setData([], [])
            self._status.setText(f"extraction failed: {exc}")
            self.fit_btn.setEnabled(False)
            return
        lines = self._lines
        self._peak_dots.setData(lines.wavelength_nm, lines.power_db)
        self._status.setText(
            f"{lines.n.size} lines on a {lines.frep_ghz:.4f} GHz grid "
            f"({lines.npeaks} peaks, floor {lines.floor_db:.1f} dBm)"
        )
        self.fit_btn.setEnabled(True)

    # ---- fitting ----------------------------------------------------------

    def _start_fit(self):
        if self._lines is None or self._worker is not None:
            return
        # re-extract so edited spacing/prominence settings take effect
        self._extract()
        if self._lines is None:
            return
        self.fit_btn.setEnabled(False)
        self._status.setText("fitting…")
        self._worker = _FitWorker(self._lines, self)
        self._worker.finished_ok.connect(self._show_fit)
        self._worker.failed.connect(self._fit_failed)
        self._worker.finished.connect(self._fit_done)
        self._worker.start()

    def _fit_done(self):
        self._worker = None
        self.fit_btn.setEnabled(self._lines is not None)

    def _fit_failed(self, message: str):
        self._status.setText(f"fit failed: {message}")

    def _show_fit(self, fit: CombFit):
        self._fit = fit
        lines = self._lines
        if lines is None:
            return
        # display what the fit minimises: the model clipped at the floor
        clipped = np.maximum(fit.model_db, lines.floor_db)
        self._fit_curve.setData(lines.wavelength_nm, clipped)
        self._res_dots.setData(lines.wavelength_nm, clipped - lines.power_db)

        deg = 180.0 / np.pi
        sig = fit.sigma
        self._phase_label.setText(_format_pm(fit.phase * deg, sig["phase"] * deg, 1, "°"))
        self._twin_label.setText(
            f"{fit.twin_phase * deg:.1f}° at {fit.twin_rms_db:.2f} dB rms (best {fit.rms_db:.2f})"
        )
        self._value_labels["beta"].setText(
            _format_pm(fit.beta, sig["beta"], 2, " rad") + f"  ({fit.beta / np.pi:.2f} π)"
        )
        self._value_labels["m"].setText(_format_pm(fit.m, sig["m"], 2, " rad"))
        self._value_labels["theta_bias"].setText(
            _format_pm(fit.theta_bias * deg, sig["theta_bias"] * deg, 1, "°")
        )
        self._value_labels["offset_db"].setText(
            _format_pm(fit.offset_db, sig["offset_db"], 1, " dB")
        )
        self._value_labels["rms"].setText(f"{fit.rms_db:.2f} dB over {lines.n.size} lines")
        carrier_nm = _C_NM_GHZ / (lines.nu0_ghz + fit.n0 * lines.frep_ghz)
        self._value_labels["grid"].setText(f"{lines.frep_ghz:.4f} GHz, carrier {carrier_nm:.3f} nm")
        self._status.setText(f"fit done — rms residual {fit.rms_db:.2f} dB")


if __name__ == "__main__":
    raise SystemExit(main())
