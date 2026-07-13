"""Tests for the KTL-native GUI (ktl/ktl_gui.py) — pure helpers plus a
demo-mode smoke test when a display is available."""

import importlib.util
import os
import sys
import time
from pathlib import Path

import pytest

GUI_PATH = Path(__file__).resolve().parents[1] / "ktl" / "ktl_gui.py"


@pytest.fixture(scope="module")
def gui_module():
    spec = importlib.util.spec_from_file_location("ktl_gui", GUI_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_decode_state(gui_module):
    state, subs = gui_module.decode_state(30030)
    assert state == "FULL COMB"
    assert all(subs.values())
    assert gui_module.decode_state(15015)[0] == "STANDBY"
    assert gui_module.decode_state(1)[0] == "OFF"
    # 30030/3: EDFA23's prime factor missing, everything else on
    state, subs = gui_module.decode_state(10010)
    assert state == "PARTIAL"
    assert subs["EDFA23"] is False
    assert subs["Pritel"] is True
    assert gui_module.decode_state("garbage")[0] == "UNKNOWN"
    assert gui_module.decode_state(None)[0] == "UNKNOWN"


def test_heartbeat_ok(gui_module):
    assert gui_module.heartbeat_ok(str(int(time.time()))) is True
    assert gui_module.heartbeat_ok(str(int(time.time()) - 300)) is False
    assert gui_module.heartbeat_ok(None) is False
    assert gui_module.heartbeat_ok("not-a-number") is False


def _display_available() -> bool:
    if sys.platform == "darwin" or os.name == "nt":
        return True
    return bool(os.environ.get("DISPLAY"))


@pytest.mark.skipif(not _display_available(), reason="no display for tkinter")
def test_demo_mode_smoke():
    """Run the Tk smoke test in a subprocess: Tk and Qt (used by the PyQt
    GUI tests) crash when mixed in one process."""
    import subprocess

    script = f"""
import importlib.util, time
spec = importlib.util.spec_from_file_location("ktl_gui", {str(GUI_PATH)!r})
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
import tkinter as tk
root = tk.Tk(); root.withdraw()
gui = mod.CombGui(root, mod.DemoBackend(), period_s=0.05)
for _ in range(4):
    root.update(); time.sleep(0.06)
assert gui.banner.cget("text") == "STANDBY", gui.banner.cget("text")
gui.backend.write("LFC_SET_FULL_COMB", 1)
for _ in range(3):
    time.sleep(0.06); root.update()
assert gui.banner.cget("text") == "FULL COMB", gui.banner.cget("text")
root.destroy()
print("OK")
"""
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=60
    )
    if "cannot open a display" in result.stderr or "no display" in result.stderr:
        pytest.skip("tkinter cannot open a display")
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout
