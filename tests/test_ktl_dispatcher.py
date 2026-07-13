"""Tests for the HTTP half of the Keck dispatcher (ktl/combd.sin).

kroot's DFW/ktl modules only exist on the observatory hosts, so they are
stubbed out; what we exercise here is everything the dispatcher does over
the wire — health probes, snapshot-cached reads, writes, and error
mapping — against a real keckogeco sim server.
"""

import sys
import threading
import time
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _load_combd():
    """Exec ktl/combd.sin with DFW/ktl stubbed; return its namespace."""
    dfw = types.ModuleType("DFW")

    class _Base:
        def __init__(self, *args, **kwargs):
            pass

    keyword_mod = types.SimpleNamespace(
        Basic=_Base,
        Boolean=type("Boolean", (_Base,), {}),
        Double=type("Double", (_Base,), {}),
        DoubleArray=type("DoubleArray", (_Base,), {}),
        Enumerated=type("Enumerated", (_Base,), {}),
        Integer=type("Integer", (_Base,), {}),
        String=type("String", (_Base,), {}),
    )
    dfw.Keyword = keyword_mod
    dfw.Service = _Base
    sys.modules.setdefault("DFW", dfw)
    sys.modules.setdefault("ktl", types.ModuleType("ktl"))

    source = (REPO / "ktl" / "combd.sin").read_text()
    namespace = {"__name__": "combd_under_test"}
    exec(compile(source, "combd.sin", "exec"), namespace)  # noqa: S102
    return namespace


@pytest.fixture(scope="module")
def combd():
    return _load_combd()


@pytest.fixture(scope="module")
def server_url():
    import uvicorn

    from keckogeco.config import load_config
    from keckogeco.server.app import create_app

    config = load_config(REPO / "config" / "instruments.example.toml")
    app = create_app(config, sim=True, poll_s=0)
    server = uvicorn.Server(uvicorn.Config(app, port=8901, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    import urllib.request

    while time.time() < deadline:
        try:
            urllib.request.urlopen("http://127.0.0.1:8901/api/v1/health", timeout=1)
            break
        except OSError:
            time.sleep(0.2)
    yield "http://127.0.0.1:8901"
    server.should_exit = True


def test_format_value(combd):
    fmt = combd["format_value"]
    assert fmt(True) == "1"
    assert fmt(False) == "0"
    assert fmt(None) is None
    assert fmt([1.0, 2.5]) == "1.0 2.5"
    assert fmt(16e9) == "16000000000.0"
    assert fmt("STANDBY") == "STANDBY"


def test_probe_and_read(combd, server_url):
    client = combd["HttpClient"](server_url)
    assert client.probe() is True
    # never-cached keyword: falls through to a fresh single read
    value = client.read_keyword("LFC_EDFA27_P")
    assert isinstance(value, (int, float))
    # now served from the snapshot without another bulk fetch
    calls_before = client.ncalls
    client.read_keyword("LFC_EDFA27_P")
    assert client.ncalls == calls_before  # <2s old snapshot, no HTTP


def test_write_roundtrip_and_errors(combd, server_url):
    client = combd["HttpClient"](server_url)
    client.write_keyword("LFC_EDFA27_P", "150")
    client._snapshot_time = 0  # force snapshot refresh
    assert client.read_keyword("LFC_EDFA27_P") == pytest.approx(150.0)

    with pytest.raises(RuntimeError, match="above maximum"):
        client.write_keyword("LFC_EDFA27_P", "700")  # HTTP 400 -> detail

    with pytest.raises(RuntimeError, match="rejected"):
        client.write_keyword("NOPE", "1")  # HTTP 404


def test_probe_unreachable(combd):
    client = combd["HttpClient"]("http://127.0.0.1:1", timeout=0.5)
    assert client.probe() is False
