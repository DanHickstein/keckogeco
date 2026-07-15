"""End-to-end REST API tests against a --sim controller."""

import math
import pathlib

import pytest
from fastapi.testclient import TestClient

from keckogeco.config import load_config
from keckogeco.server.app import create_app

EXAMPLE = pathlib.Path(__file__).parent.parent / "config" / "instruments.example.toml"


@pytest.fixture
def client():
    config = load_config(EXAMPLE)
    app = create_app(config, sim=True, poll_s=0)  # no background poller in tests
    with TestClient(app) as test_client:
        yield test_client


def test_web_status_page(client):
    """The static status page is served at / without auth."""
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "keckogeco" in response.text
    assert "/api/v1" in response.text  # the page talks to the versioned API
    # status-only by design: the page must never invoke mutating endpoints
    assert "/actions/" not in response.text
    assert 'method: "POST"' not in response.text
    assert 'method: "DELETE"' not in response.text
    assert 'method: "PUT"' not in response.text


def test_health(client):
    body = client.get("/api/v1/health").json()
    assert body["status"] == "ok"
    assert body["sim"] is True
    assert "edfa27" in body["devices_online"]
    assert body["keywords_bound"] >= 30


def test_read_keyword_fresh(client):
    body = client.get("/api/v1/keywords/LFC_EDFA27_P", params={"fresh": 1}).json()
    assert body["name"] == "LFC_EDFA27_P"
    assert body["value"] == pytest.approx(0.0)


def test_write_then_read_roundtrip(client):
    response = client.put("/api/v1/keywords/LFC_EDFA27_P", json={"value": "150"})
    assert response.status_code == 200
    assert response.json()["value"] == pytest.approx(150.0)
    body = client.get("/api/v1/keywords/LFC_EDFA27_P").json()  # cached
    assert body["value"] == pytest.approx(150.0)


def test_write_validation_rejected(client):
    response = client.put("/api/v1/keywords/LFC_EDFA27_P", json={"value": 700})
    assert response.status_code == 400
    assert "maximum" in response.json()["detail"]


def test_unknown_keyword_404(client):
    assert client.get("/api/v1/keywords/NOPE").status_code == 404
    assert client.put("/api/v1/keywords/NOPE", json={"value": 1}).status_code == 404


def test_unbound_keyword_501(client):
    # VOA wavelength keywords stay unbound until the units are identified
    # on-site and their config blocks are renamed (see AGENTS.md)
    response = client.get("/api/v1/keywords/LFC_VOA1310_ATTEN", params={"fresh": 1})
    assert response.status_code == 501


def test_bulk_snapshot_populates_after_reads(client):
    client.get("/api/v1/keywords/LFC_EDFA27_P", params={"fresh": 1})
    body = client.get("/api/v1/keywords").json()
    assert "LFC_EDFA27_P" in body
    assert body["LFC_EDFA27_P"]["units"] == "mW"


def test_json_value_sanitizes_nan_inside_arrays():
    """The rack DAQ's unconnected ch7 reads NaN; inside the LFC_TEMP_TEST1
    list it must become null explicitly — whether clients see null must not
    depend on how the installed FastAPI version encodes non-finite floats
    (the sim DAQ never returns NaN, so unit-test it)."""
    from keckogeco.server.app import _json_value

    assert _json_value(math.nan) is None
    assert _json_value(21.5) == 21.5
    assert _json_value([21.5, math.nan, math.inf]) == [21.5, None, None]


def test_state_endpoint(client):
    body = client.get("/api/v1/state").json()
    assert body["state"] == "OFF"
    assert body["legacy_code"] == 1
    client.put("/api/v1/keywords/LFC_PTAMP_ONOFF", json={"value": "1"})
    assert client.get("/api/v1/state").json()["subsystems"]["ptamp"] is True


def test_schema_endpoint(client):
    body = client.get("/api/v1/schema").json()
    assert len(body) == 85  # 77 baseline + additions in ktl/keyword-changes.md
    assert body["LFC_EDFA27_P"]["max"] == 630
    assert body["LFC_EDFA27_P"]["bound"] is True
    assert body["LFC_TEMP_TEST2"]["bound"] is True  # daq_eocb board in the example config
    assert body["LFC_VOA1310_ATTEN"]["bound"] is False  # VOAs not yet identified by wavelength


def test_devices_endpoint(client):
    body = client.get("/api/v1/devices").json()
    assert body["edfa27"]["address"]  # the GUI puts this in panel titles
    assert body["edfa27"]["online"] is True
    assert body["osa"]["address"] == "GPIB0::30::INSTR"


def test_new_monitor_keywords_bound(client):
    body = client.get("/api/v1/schema").json()
    for name in (
        "LFC_EDFA27_OUTPUT_POWER_MONITOR",
        "LFC_EDFA13_INPUT_POWER_MONITOR",
        "LFC_PTAMP_IN",
        "LFC_PTAMP_INTERLOCK_V",
    ):
        assert body[name]["bound"] is True, name
    volts = client.get("/api/v1/keywords/LFC_PTAMP_INTERLOCK_V", params={"fresh": 1}).json()
    assert 0.0 <= volts["value"] <= 5.0


def test_im_scan_endpoint(client):
    import time

    response = client.post(
        "/api/v1/im/scan",
        json={"v_start": -1.0, "v_stop": 0.0, "v_step": 0.05, "settle_s": 0.0},
    )
    assert response.status_code == 200
    assert response.json()["name"] == "im_bias_scan"
    # sim skips the settle waits, so the sweep finishes almost immediately
    controller = client.app.state.controller
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        current = controller.executor.current()
        if current and not current["running"]:
            break
        time.sleep(0.02)
    assert current["error"] is None
    body = client.get("/api/v1/arrays/im_scan").json()
    assert len(body["x"]) == len(body["y"]) == 20
    assert body["x_label"] == "IM bias (V)"
    assert body["running"] is False


def test_im_servo_settings_and_lock_gate(client):
    # GET reads the live servo state
    body = client.get("/api/v1/im").json()
    assert body["mode"] == "MAN"
    # PUT writes the lock setpoint and returns the read-back
    body = client.put("/api/v1/im", json={"setpoint_V": 0.415}).json()
    assert body["setpoint_V"] == pytest.approx(0.415)
    assert client.put("/api/v1/im", json={"setpoint_V": 15}).status_code == 422
    # a scan is refused while the lock is engaged
    client.put("/api/v1/keywords/LFC_IM_LOCK_MODE", json={"value": "1"})
    response = client.post("/api/v1/im/scan", json={"settle_s": 0.0})
    assert response.status_code == 409
    assert "unlock" in response.json()["detail"]
    client.put("/api/v1/keywords/LFC_IM_LOCK_MODE", json={"value": "0"})
    assert client.post("/api/v1/im/scan", json={"settle_s": 0.0}).status_code == 200


def test_im_modules_inventory(client):
    body = client.get("/api/v1/im/modules").json()["modules"]
    assert len(body) == 8
    assert "SIM960" in body["3"]
    assert "SIM960" in body["5"]
    assert "SIM928" in body["2"]
    assert body["1"] is None  # empty slot: no reply


def test_im_servo_status_any_slot(client):
    body = client.get("/api/v1/im/servo/5").json()
    assert body["slot"] == 5
    assert body["output_mode"] in ("MAN", "PID")
    assert "measure_input_V" in body
    assert client.get("/api/v1/im/servo/9").status_code == 400


def test_im_scan_validation(client):
    # inverted range -> 400 from the handler's cross-field check
    response = client.post("/api/v1/im/scan", json={"v_start": 1.0, "v_stop": -1.0})
    assert response.status_code == 400
    assert "empty scan" in response.json()["detail"]
    # out-of-bounds bias -> 422 from pydantic (±3 V keyword limits)
    assert client.post("/api/v1/im/scan", json={"v_start": -5.0}).status_code == 422


def test_interlock_endpoint(client):
    body = client.get("/api/v1/interlock").json()
    # sim thresholds are 300/900 ADC counts, voltage 500 (10-bit over 5 V)
    assert body["low_threshold_V"] == pytest.approx(300 * 5 / 1023)
    assert body["high_threshold_V"] == pytest.approx(900 * 5 / 1023)
    assert body["low_threshold_V"] < body["voltage_V"] < body["high_threshold_V"]
    assert body["ok_to_amplify"] is True


def test_osa_settings_endpoints(client):
    body = client.get("/api/v1/osa").json()
    assert body["resolution_nm"] == pytest.approx(0.06)  # sim default = best
    assert body["resolutions_nm"][0] == 0.06
    assert body["sweep_continuous"] is True
    body = client.put(
        "/api/v1/osa", json={"start_nm": 1550, "stop_nm": 1570, "resolution_nm": 0.1}
    ).json()
    assert body["wl_start_nm"] == pytest.approx(1550.0)
    assert body["wl_stop_nm"] == pytest.approx(1570.0)
    assert body["resolution_nm"] == pytest.approx(0.1)
    # partial update leaves the rest alone
    body = client.put("/api/v1/osa", json={"sensitivity_dBm": -75}).json()
    assert body["sensitivity_dBm"] == pytest.approx(-75.0)
    assert body["wl_start_nm"] == pytest.approx(1550.0)


def test_osa_sweep_endpoint(client):
    body = client.post("/api/v1/osa/sweep", json={"mode": "stop"}).json()
    assert body["sweep_continuous"] is False
    body = client.post("/api/v1/osa/sweep", json={"mode": "continuous"}).json()
    assert body["sweep_continuous"] is True
    body = client.post("/api/v1/osa/sweep", json={"mode": "single"}).json()
    assert body["sweep_continuous"] is False  # single sweep then hold
    assert client.post("/api/v1/osa/sweep", json={"mode": "bogus"}).status_code == 422


def test_bearer_token_auth(tmp_path):
    import shutil

    cfg_file = tmp_path / "keckogeco.toml"
    shutil.copy(EXAMPLE, cfg_file)
    text = cfg_file.read_text().replace('api_token = ""', 'api_token = "secret123"')
    cfg_file.write_text(text)
    config = load_config(cfg_file)
    app = create_app(config, sim=True, poll_s=0)
    with TestClient(app) as client:
        assert client.get("/api/v1/state").status_code == 401
        ok = client.get("/api/v1/state", headers={"Authorization": "Bearer secret123"})
        assert ok.status_code == 200
        # health stays open for monitoring
        assert client.get("/api/v1/health").status_code == 200
