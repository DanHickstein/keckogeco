"""OsaSweeper: single-grab cadence, fast->slow idle decay, spectrum logs.

Timing-based tests use injected sub-second periods with generous
wait_for margins so they stay robust on slow CI runners.
"""

import time

import pytest

from keckogeco.comb.osa_sweeper import OsaSweeper
from keckogeco.config import DeviceConfig
from keckogeco.drivers.agilent_86142b import Agilent86142B
from keckogeco.spectra import load_spectrum_csv


def make_osa():
    cfg = DeviceConfig(key="osa", driver="agilent_86142b", address="GPIB0::30::INSTR", options={})
    osa = Agilent86142B.from_config(cfg, sim=True)
    osa.connect()
    return osa


def wait_for(predicate, timeout_s: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def run_sweeper(sweeper):
    """Context manager: start the thread, always stop it."""
    import contextlib

    @contextlib.contextmanager
    def _ctx():
        sweeper.start()
        try:
            yield sweeper
        finally:
            sweeper.stop()
            sweeper.join(timeout=2)
            assert not sweeper.is_alive()

    return _ctx()


def test_latest_grabs_synchronously_when_cache_is_cold():
    osa = make_osa()
    sweeper = OsaSweeper(osa)  # thread not started: latest() must still work
    data = sweeper.latest()
    assert len(data["x"]) == len(data["y"]) == 501
    assert data["time"]
    sent = osa.transport.sent
    # single-sweep grab, never continuous, gated on *OPC?
    assert sent.index("INIT:CONT 0") < sent.index("INIT:IMM") < sent.index("*OPC?")


def test_slow_cadence_and_request_grab():
    osa = make_osa()
    sweeper = OsaSweeper(osa, slow_period_s=1000.0)
    with run_sweeper(sweeper):
        assert wait_for(lambda: osa.transport.sent.count("INIT:IMM") == 1)
        time.sleep(0.2)  # long slow period: no further grabs on their own
        assert osa.transport.sent.count("INIT:IMM") == 1
        sweeper.request_grab()  # e.g. a settings change wants a fresh view
        assert wait_for(lambda: osa.transport.sent.count("INIT:IMM") == 2)


def test_fast_decays_to_slow_when_idle():
    osa = make_osa()
    sweeper = OsaSweeper(osa, fast_idle_timeout_s=0.2, slow_period_s=1000.0)
    with run_sweeper(sweeper):
        sweeper.set_mode("fast")
        assert sweeper.mode == "fast"
        assert wait_for(lambda: osa.transport.sent.count("INIT:IMM") >= 3)
        assert wait_for(lambda: sweeper.mode == "slow")  # decayed, untouched
        info = sweeper.info()
        assert info["sweep_mode"] == "slow"
        assert info["fast_remaining_s"] is None


def test_touch_keeps_fast_alive():
    osa = make_osa()
    sweeper = OsaSweeper(osa, fast_idle_timeout_s=0.4, slow_period_s=1000.0)
    with run_sweeper(sweeper):
        sweeper.set_mode("fast")
        for _ in range(4):  # keep renewing past the original deadline
            time.sleep(0.15)
            sweeper.touch()
            assert sweeper.mode == "fast"
        assert wait_for(lambda: sweeper.mode == "slow")  # stop touching


def test_spectrum_logging(tmp_path):
    osa = make_osa()
    sweeper = OsaSweeper(osa, log_dir=tmp_path, slow_period_s=0.1, log_period_s=0.1)
    with run_sweeper(sweeper):
        assert wait_for(lambda: list(tmp_path.rglob("*.csv")))
    path = next(iter(tmp_path.rglob("*.csv")))
    assert path.parent.name == time.strftime("%Y-%m-%d")  # daily folder
    x, y, metadata = load_spectrum_csv(path)
    assert len(x) == len(y) == 501
    assert metadata["instrument"] == "agilent_86142b"
    assert "resolution_nm" in metadata  # settings ride along


def test_no_relog_of_stale_spectrum(tmp_path):
    osa = make_osa()
    # grabs far apart, log check fast: only ONE file may appear, because
    # the cached spectrum is only logged once per grab serial
    sweeper = OsaSweeper(osa, log_dir=tmp_path, slow_period_s=1000.0, log_period_s=0.05)
    with run_sweeper(sweeper):
        assert wait_for(lambda: list(tmp_path.rglob("*.csv")))
        time.sleep(0.3)
        assert len(list(tmp_path.rglob("*.csv"))) == 1


def test_set_mode_validation():
    sweeper = OsaSweeper(make_osa())
    with pytest.raises(ValueError, match="sweep mode"):
        sweeper.set_mode("continuous")
