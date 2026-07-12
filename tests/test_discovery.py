"""Tests for the discovery module (pure functions + config writing)."""

from pathlib import Path

from keckogeco.config import load_config
from keckogeco.discovery import (
    PROBES,
    PROBES_BY_NAME,
    address_for,
    classify_idn,
    extract_token,
    make_key,
    save_config,
    visa_addr_for,
)


def test_probe_names_unique():
    assert len(PROBES_BY_NAME) == len(PROBES)


def test_classify_idn():
    device, driver, options = classify_idn(b"GW-INSTEK,GPD-4303S,SN123,V1.0")
    assert driver == "instek_psu"
    assert options == {"model": "GPD-4303S"}
    device, driver, options = classify_idn(b"Stanford_Research_Systems,SIM900,s/n1,v2")
    assert driver == "srs_sim900"
    device, driver, _ = classify_idn(b"ACME,WIDGET,1,2")
    assert driver == "?"
    assert "Unrecognized" in device


def test_extract_token_idn_model_field():
    assert extract_token("*IDN? @ 9600", "GW-INSTEK,GPD-4303S,SN123,V1.0") == "GPD-4303S"
    assert extract_token(":CAL:SYS:MODEL? @ 19200", "AEDFA-PA-30-B-FA") == "AEDFA-PA-30-B-FA"
    assert extract_token("SN? @ 9600 (OZ Optics)", "Serial No.: 123456-01, Cal") == "NO-123456-01"
    # FS725 replies with a bare serial (escaped CR from printable())
    assert extract_token("SN? @ 9600", "34437\\r") == "SN34437"


def test_address_for():
    assert visa_addr_for("COM13") == "ASRL13::INSTR"
    assert address_for({"visa": True}, "COM13") == "ASRL13::INSTR"
    assert address_for({"visa": False}, "COM13") == "COM13"
    assert address_for({"driver": "waveshaper"}, "COM9", "WS201904D") == "SN201904"


def test_make_key_uniqueness():
    existing = {"amonics_edfa_A": {}}
    key1 = make_key("amonics_edfa", "A", None, existing)
    assert key1 == "amonics_edfa_A_2"
    assert make_key("?", None, "FT123", {}) == "unknown_serial_device_FT123"


def test_save_config_preserves_other_sections(tmp_path: Path):
    config_path = tmp_path / "keckogeco.toml"
    config_path.write_text("# my comment\n[server]\nport = 9999\n", encoding="utf-8")
    instruments = {
        "edfa27": {
            "driver": "amonics_edfa",
            "address": "ASRL13::INSTR",
            "device": "Amonics EDFA",
            "usb_serial": "FT0001",
            "probe": ":CAL:SYS:MODEL? @ 19200",
            "match_token": "AEDFA-PA-30-B-FA",
        },
        "mystery": {"driver": "?", "address": "COM9", "device": "Unknown device"},
        "eaton": {"driver": "eaton_pdu", "address": "ASRL7::INSTR", "device": "Eaton PDU"},
    }
    save_config(instruments, config_path)
    text = config_path.read_text()
    assert "# my comment" in text  # untouched sections survive

    cfg = load_config(config_path)
    assert cfg.server.port == 9999
    assert cfg.devices["edfa27"].driver == "amonics_edfa"
    assert cfg.devices["edfa27"].options["usb_serial"] == "FT0001"
    assert cfg.devices["mystery"].enabled is False  # unidentified -> disabled
    assert cfg.devices["eaton"].enabled is False  # driver not ported -> disabled
    assert "not yet ported" in text

    # and the drivers can consume the block (metadata keys ignored)
    from keckogeco.drivers.amonics_edfa import AmonicsEDFA

    inst = AmonicsEDFA.from_config(cfg.devices["edfa27"], sim=True)
    assert inst.name == "edfa27"
