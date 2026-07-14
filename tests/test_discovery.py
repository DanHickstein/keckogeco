"""Tests for the discovery module (pure functions + config writing)."""

from pathlib import Path

from keckogeco.config import load_config
from keckogeco.discovery import (
    PROBES,
    PROBES_BY_NAME,
    address_for,
    classify_idn,
    extract_token,
    load_existing,
    make_key,
    resolve_port,
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


def test_normalize_visa_addr():
    from keckogeco.discovery import find_existing_key, normalize_visa_addr

    with_iface = "USB0::0x0699::0x03A6::C031910::0::INSTR"
    without = "USB0::0x0699::0x03A6::C031910::INSTR"
    assert normalize_visa_addr(with_iface) == without
    assert normalize_visa_addr(without) == without
    # GPIB addresses must NOT be rewritten
    assert normalize_visa_addr("GPIB0::10::INSTR") == "GPIB0::10::INSTR"

    instruments = {
        "tds": {"driver": "tds2024c", "address": without},
        "ws": {"driver": "waveshaper", "address": "SN201904", "usb_serial": "WS201904D"},
    }
    assert find_existing_key(instruments, None, with_iface) == "tds"
    assert find_existing_key(instruments, "WS201904D", None) == "ws"
    assert find_existing_key(instruments, "OTHER", "GPIB0::30::INSTR") is None


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


def test_rediscovery_preserves_curated_options(tmp_path: Path):
    """load_existing -> save_config round-trip must keep human-added keys
    (mode, channel, enabled = false); a rediscovery run clobbered them all
    on 2026-07-12."""
    config_path = tmp_path / "keckogeco.toml"
    config_path.write_text(
        "\n".join(
            [
                "[server]",
                "port = 8000",
                "",
                "[devices.edfa27]",
                'driver = "amonics_edfa"',
                'address = "COM17"',
                'mode = "APC"',
                'usb_serial = "FT0002"',
                "",
                "[devices.rf_osc_psu]",
                'driver = "instek_psu"',
                'address = "COM14"',
                'model = "GPD-4303S"',
                "channel = 2",
                "",
                "[devices.spare_voa]",
                'driver = "oz_voa"',
                'address = "COM5"',
                "enabled = false",
                "",
            ]
        ),
        encoding="utf-8",
    )
    entries = load_existing(config_path)
    entries["edfa27"]["response"] = "AEDFA-PM-27-R-FA"  # transient probe data
    save_config(entries, config_path)

    cfg = load_config(config_path)
    assert cfg.devices["edfa27"].options["mode"] == "APC"
    assert cfg.devices["rf_osc_psu"].options["channel"] == 2
    assert cfg.devices["rf_osc_psu"].options["model"] == "GPD-4303S"
    assert cfg.devices["spare_voa"].enabled is False  # user-disabled stays disabled
    assert "response" not in cfg.devices["edfa27"].options  # transients stay out
    assert "port" not in cfg.devices["edfa27"].options


def test_save_config_keeps_note_and_missing_since_and_writes_backup(tmp_path: Path):
    """Curated notes and the missing_since tag survive the round-trip, and
    the pre-run file is kept as .bak."""
    config_path = tmp_path / "keckogeco.toml"
    config_path.write_text(
        "\n".join(
            [
                "[devices.rio]",
                'driver = "orion_laser"',
                'address = "COM5"',
                "enabled = false",
                'note = "suspected Rio ORION; bare Prolific adapter"',
                "",
                "[devices.tec_tc720_AG0JO6EHA]",
                'driver = "tec_tc720"',
                'address = "COM13"',
                "enabled = false",
                'usb_serial = "AG0JO6EHA"',
                'missing_since = "2026-07-13T14:00:00"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    original = config_path.read_text(encoding="utf-8")
    entries = load_existing(config_path)
    save_config(entries, config_path)

    cfg = load_config(config_path)
    assert cfg.devices["rio"].enabled is False
    assert cfg.devices["rio"].options["note"] == "suspected Rio ORION; bare Prolific adapter"
    assert cfg.devices["tec_tc720_AG0JO6EHA"].options["missing_since"].startswith("2026-07-13")
    backup = config_path.with_name(config_path.name + ".bak")
    assert backup.read_text(encoding="utf-8") == original


def test_resolve_port_prefers_usb_serial_over_stale_port_name():
    class FakePort:
        def __init__(self, device):
            self.device = device

    ports_by_serial = {"AG0JO6EHA": FakePort("COM42")}
    ports_by_name = {"COM13": FakePort("COM13"), "COM42": FakePort("COM42")}
    # adapter renumbered COM13 -> COM42: the serial wins over the stale name
    entry = {"usb_serial": "AG0JO6EHA", "port": "COM13"}
    assert resolve_port(entry, ports_by_serial, ports_by_name) == "COM42"
    # no serial (Prolific): fall back to the recorded port name
    assert resolve_port({"port": "COM13"}, ports_by_serial, ports_by_name) == "COM13"
    # adapter gone entirely
    assert resolve_port({"port": "COM99"}, ports_by_serial, ports_by_name) is None


def test_load_existing_marks_only_serial_devices_as_ports(tmp_path: Path):
    """Only COM/ASRL addresses get a "port" key. A bare MCC serial or an
    IP address must not - the verify loop treats "port" entries as COM
    devices and drops them when no adapter matches (that deleted the daq
    blocks on 2026-07-13)."""
    config_path = tmp_path / "keckogeco.toml"
    config_path.write_text(
        "\n".join(
            [
                "[devices.edfa27]",
                'driver = "amonics_edfa"',
                'address = "COM17"',
                "",
                "[devices.daq]",
                'driver = "usb2408"',
                'address = "205F843"',
                "",
                "[devices.rp1]",
                'driver = "red_pitaya"',
                'address = "10.0.0.5"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    entries = load_existing(config_path)
    assert entries["edfa27"]["port"] == "COM17"
    assert "port" not in entries["daq"]
    assert "port" not in entries["rp1"]
