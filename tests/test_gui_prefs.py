"""GUI preferences file round-trip (no Qt needed)."""

from keckogeco.gui import prefs


def test_missing_file_gives_empty(tmp_path):
    assert prefs.load_section("osa_defaults", path=tmp_path / "gui.toml") == {}


def test_save_and_load_roundtrip(tmp_path):
    path = tmp_path / "gui.toml"
    values = {"start_nm": 1548.0, "stop_nm": 1572.0, "resolution_nm": 0.2}
    prefs.save_section("osa_defaults", values, path=path)
    assert prefs.load_section("osa_defaults", path=path) == values


def test_save_preserves_other_sections(tmp_path):
    path = tmp_path / "gui.toml"
    path.write_text("# hand-written comment\n[window]\nwidth = 880\n", encoding="utf-8")
    prefs.save_section("osa_defaults", {"start_nm": 1550.0}, path=path)
    assert prefs.load_section("window", path=path) == {"width": 880}
    assert "hand-written comment" in path.read_text(encoding="utf-8")


def test_malformed_file_gives_empty(tmp_path):
    path = tmp_path / "gui.toml"
    path.write_text("not [valid toml", encoding="utf-8")
    assert prefs.load_section("osa_defaults", path=path) == {}


def test_committed_prefs_file_matches_factory_defaults():
    """config/gui.toml ships the factory view so a fresh checkout and the
    code agree; 'Save as default' rewrites it."""
    saved = prefs.load_section("osa_defaults")
    assert set(saved) == {"start_nm", "stop_nm", "resolution_nm", "sensitivity_dBm"}
