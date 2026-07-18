"""Comb-fit tool: model properties, line extraction, and parameter
recovery on synthetic spectra with known ground truth."""

import numpy as np
import pytest

pytest.importorskip("PyQt6")

from keckogeco.gui.combfit import (  # noqa: E402
    _C_NM_GHZ,
    extract_comb_lines,
    fit_comb,
    model_line_powers_db,
)

TRUTH = {"beta": 2.0, "m": 1.2, "theta_bias": 1.4, "phase": 0.9}


def phase_error(fitted, truth):
    """Distance to the closer of phase and its π−phase twin (the envelope
    pins sin(phase); the two are separated only by ≲0.1 dB effects)."""
    wrap = lambda a: (a + np.pi) % (2 * np.pi) - np.pi  # noqa: E731
    return min(abs(wrap(fitted - truth)), abs(wrap(fitted - (np.pi - truth))))


def synth_spectrum(
    beta,
    m,
    theta_bias,
    phase,
    frep_ghz=16.0,
    center_nm=1560.0,
    span_nm=20.0,
    res_nm=0.06,
    step_nm=0.02,
    floor_dbm=-70.0,
    peak_dbm=-15.0,
):
    """OSA-like trace of the model comb: Gaussian lines on a noise floor."""
    n = np.arange(-80, 81)
    line_lin = 10 ** (model_line_powers_db(beta, m, theta_bias, phase, n) / 10)
    nu0 = _C_NM_GHZ / center_nm
    wl = np.arange(center_nm - span_nm / 2, center_nm + span_nm / 2, step_nm)
    nu = _C_NM_GHZ / wl
    sigma = (res_nm * _C_NM_GHZ / center_nm**2) / 2.355  # OSA resolution, GHz
    gauss = np.exp(-((nu[None, :] - (nu0 + n * frep_ghz)[:, None]) ** 2) / (2 * sigma**2))
    spec = line_lin @ gauss
    spec = spec / spec.max() * 10 ** (peak_dbm / 10) + 10 ** (floor_dbm / 10)
    return wl, 10 * np.log10(spec)


def test_model_mirror_symmetry():
    """phi -> -phi mirrors the comb envelope about the carrier."""
    n = np.arange(-40, 41)
    forward = model_line_powers_db(2.0, 1.0, 1.2, 0.7, n)
    mirrored = model_line_powers_db(2.0, 1.0, 1.2, -0.7, -n)
    real = forward > -150  # below that the FFT's own roundoff dominates
    assert np.allclose(forward[real], mirrored[real], atol=1e-6)
    # and a nonzero phase really is asymmetric (there is something to fit)
    assert np.abs(forward[real] - forward[::-1][real]).max() > 1.0


def test_extract_comb_lines_grid():
    wl, db = synth_spectrum(**TRUTH)
    lines = extract_comb_lines(wl, db, frep_ghz=16.0, prominence_db=5.0)
    assert abs(lines.frep_ghz - 16.0) < 0.02
    assert lines.n.size >= 10
    assert lines.npeaks >= 8
    assert lines.floor_db < -60
    # grid wavelengths must land on the synthesized line positions
    nu_lines = lines.nu0_ghz + lines.n * lines.frep_ghz
    offsets = (nu_lines - _C_NM_GHZ / 1560.0) / 16.0
    assert np.allclose(offsets, np.round(offsets), atol=0.05)


def test_extract_rejects_lineless_trace():
    wl = np.linspace(1550, 1570, 500)
    with pytest.raises(ValueError, match="comb lines"):
        extract_comb_lines(wl, np.full(wl.size, -70.0))


def test_fit_recovers_known_parameters():
    wl, db = synth_spectrum(**TRUTH)
    lines = extract_comb_lines(wl, db)
    fit = fit_comb(lines)
    assert fit.beta == pytest.approx(TRUTH["beta"], abs=0.1)
    assert fit.m == pytest.approx(TRUTH["m"], abs=0.1)
    assert fit.theta_bias == pytest.approx(TRUTH["theta_bias"], abs=0.1)
    assert phase_error(fit.phase, TRUTH["phase"]) < 0.05
    assert np.sin(fit.phase) == pytest.approx(np.sin(TRUTH["phase"]), abs=0.05)
    assert fit.rms_db < 1.0
    assert set(fit.sigma) == {"beta", "m", "theta_bias", "phase", "offset_db"}
    # the twin is the other member of the {phase, pi - phase} pair, and
    # on clean model data it fits nearly as well as the primary
    assert phase_error(fit.twin_phase, TRUTH["phase"]) < 0.05
    assert abs(fit.twin_phase - fit.phase) > 0.2
    assert fit.twin_rms_db < 1.0


def test_fit_pins_phase_sign_from_asymmetry():
    """A mirrored spectrum must come back with sin(phase) negated."""
    wl, db = synth_spectrum(**{**TRUTH, "phase": -TRUTH["phase"]})
    fit = fit_comb(extract_comb_lines(wl, db))
    assert phase_error(fit.phase, -TRUTH["phase"]) < 0.05
    assert np.sin(fit.phase) == pytest.approx(-np.sin(TRUTH["phase"]), abs=0.05)


def test_combfit_window_loads_and_fits(qtbot, tmp_path):
    pytest.importorskip("pyqtgraph")
    from keckogeco.gui.combfit import CombFitWindow
    from keckogeco.gui.spectra import save_spectrum_csv

    wl, db = synth_spectrum(**TRUTH)
    path = tmp_path / "osa_synthetic.csv"
    save_spectrum_csv(path, list(wl), list(db), {"start_nm": wl[0], "stop_nm": wl[-1]})

    window = CombFitWindow()
    qtbot.addWidget(window)
    window.load_path(path)
    assert window._lines is not None
    assert window._lines.n.size >= 10

    qtbot.waitUntil(lambda: window._fit is not None, timeout=60000)
    assert phase_error(window._fit.phase, TRUTH["phase"]) < 0.05
    assert "°" in window._phase_label.text()
    assert "rms" in window._status.text()
