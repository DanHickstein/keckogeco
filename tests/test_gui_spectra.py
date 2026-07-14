"""Spectrum CSV round-trip (no Qt needed)."""

import pytest

from keckogeco.gui.spectra import load_spectrum_csv, save_spectrum_csv


def test_roundtrip_with_metadata(tmp_path):
    path = tmp_path / "spec.csv"
    save_spectrum_csv(
        path,
        [1550.0, 1560.0, 1570.0],
        [-40.0, -20.0, -35.5],
        {"resolution_nm": 0.06, "sensitivity_dBm": -60.0},
    )
    x, y, metadata = load_spectrum_csv(path)
    assert x == [1550.0, 1560.0, 1570.0]
    assert y == [-40.0, -20.0, -35.5]
    assert metadata["resolution_nm"] == "0.06"
    assert metadata["sensitivity_dBm"] == "-60.0"
    assert "saved" in metadata
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# keckogeco OSA spectrum")
    assert "wavelength_nm,power_dBm" in text


def test_length_mismatch_rejected(tmp_path):
    with pytest.raises(ValueError, match="points"):
        save_spectrum_csv(tmp_path / "bad.csv", [1.0, 2.0], [3.0], {})


def test_load_tolerates_foreign_csv(tmp_path):
    """A plain two-column CSV without our headers still loads."""
    path = tmp_path / "foreign.csv"
    path.write_text("wl,p\n1550,-40\n1551,-41\n", encoding="utf-8")
    x, y, metadata = load_spectrum_csv(path)
    assert x == [1550.0, 1551.0]
    assert metadata == {}


def test_load_rejects_no_data(tmp_path):
    path = tmp_path / "empty.csv"
    path.write_text("# only a comment\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no numeric"):
        load_spectrum_csv(path)
