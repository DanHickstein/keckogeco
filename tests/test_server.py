"""End-to-end REST API tests against a --sim controller."""

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
    response = client.get("/api/v1/keywords/LFC_TEMP_TEST2", params={"fresh": 1})
    assert response.status_code == 501


def test_bulk_snapshot_populates_after_reads(client):
    client.get("/api/v1/keywords/LFC_EDFA27_P", params={"fresh": 1})
    body = client.get("/api/v1/keywords").json()
    assert "LFC_EDFA27_P" in body
    assert body["LFC_EDFA27_P"]["units"] == "mW"


def test_state_endpoint(client):
    body = client.get("/api/v1/state").json()
    assert body["state"] == "OFF"
    assert body["legacy_code"] == 1
    client.put("/api/v1/keywords/LFC_PTAMP_ONOFF", json={"value": "1"})
    assert client.get("/api/v1/state").json()["subsystems"]["ptamp"] is True


def test_schema_endpoint(client):
    body = client.get("/api/v1/schema").json()
    assert len(body) == 77
    assert body["LFC_EDFA27_P"]["max"] == 630
    assert body["LFC_EDFA27_P"]["bound"] is True
    assert body["LFC_TEMP_TEST2"]["bound"] is False  # daq_eocb not in the example config


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
