"""Tests for keckogeco.config."""

import pytest

from keckogeco.config import ConfigError, find_config_file, load_config, parse_config

EXAMPLE = "config/instruments.example.toml"


def test_example_file_parses(tmp_path, monkeypatch):
    """The committed example config must always load cleanly."""
    import pathlib
    import shutil

    repo_example = pathlib.Path(__file__).parent.parent / EXAMPLE
    target = tmp_path / "keckogeco.toml"
    shutil.copy(repo_example, target)
    cfg = load_config(target)
    assert cfg.server.port == 8000
    assert "edfa27" in cfg.devices
    assert cfg.devices["edfa27"].driver == "amonics_edfa"
    assert cfg.devices["ptamp"].name == "Pritel high-power amplifier"
    # extra keys land in options
    assert cfg.devices["rf_osc_psu"].options["model"] == "GPD-4303S"


def test_missing_file_gives_helpful_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("KECKOGECO_CONFIG", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(ConfigError, match="keckogeco-find"):
        find_config_file()


def test_env_var_search_path(tmp_path, monkeypatch):
    cfg_file = tmp_path / "custom.toml"
    cfg_file.write_text("[server]\nport = 9000\n")
    monkeypatch.setenv("KECKOGECO_CONFIG", str(cfg_file))
    cfg = load_config()
    assert cfg.server.port == 9000
    assert cfg.source == cfg_file


def test_device_missing_required_keys():
    with pytest.raises(ConfigError, match=r"\[devices.broken\].*address"):
        parse_config({"devices": {"broken": {"driver": "amonics_edfa"}}})


def test_disabled_devices_filtered():
    data = {
        "devices": {
            "a": {"driver": "x", "address": "COM1"},
            "b": {"driver": "y", "address": "COM2", "enabled": False},
        }
    }
    cfg = parse_config(data)
    assert set(cfg.devices) == {"a", "b"}
    assert set(cfg.enabled_devices()) == {"a"}


def test_invalid_toml_raises_config_error(tmp_path):
    bad = tmp_path / "keckogeco.toml"
    bad.write_text("this is not toml [[[")
    with pytest.raises(ConfigError, match="Could not parse"):
        load_config(bad)
