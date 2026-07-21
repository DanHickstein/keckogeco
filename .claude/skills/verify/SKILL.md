---
name: verify
description: How to verify keckogeco changes end-to-end on this machine (the comb laptop) — drive the real GUI/server, capture screenshots.
---

# Verifying keckogeco changes on LAPTOP-LFC2

This machine is the deployment target: the **production server is usually
live on http://127.0.0.1:8000, non-sim, owning the real rack hardware**.
(Always 127.0.0.1, never localhost — localhost resolves to ::1 first and
costs ~2 s per fresh connection; see AGENTS.md.)
Check `GET /api/v1/health` first (no auth configured — no bearer token in
`config/keckogeco.toml`). Never start a second non-sim server: COM ports
are exclusive. Never restart the production server without asking Dan.

## Server changes

Run a second, simulated instance on another port (reads real code, fake
hardware):

```
python -m keckogeco.server.app --sim --port 8001 --poll 1 --config config\instruments.example.toml
```

Then hit `http://127.0.0.1:8001/api/v1/...` with Invoke-WebRequest.
Read-only GETs against the **production** server on 8000 are fine and give
real-rack values (e.g. LFC_TEMP_TEST1 ch7 is genuinely NaN→null there).

## GUI changes — offscreen screenshot harness

The GUI is a pure REST client; pointing it at the live server is safe
**if you keep it read-only**: disconnect `array_poller.arrays_available`
before the first poll, otherwise wiring the OSA panel pushes sweep
settings to the real instrument.

```python
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")  # else all text renders as tofu boxes
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication
from keckogeco.gui.client import KeckogecoClient
from keckogeco.gui.mainwindow import MainWindow
from keckogeco.gui.theme import apply_dark_theme  # note: apply_dark_theme, not apply_theme

app = QApplication([])
apply_dark_theme(app)
window = MainWindow(KeckogecoClient("http://127.0.0.1:8000"))
window.array_poller.arrays_available.disconnect()  # read-only: no OSA/IM panel wire-up
window.resize(1500, 980)
window.show()

def grab():
    window.grab().save("gui_overview.png")  # or box.grab() on a findChildren(QGroupBox) hit
    window.poller.stop(); window.array_poller.stop(); window.writer.stop(); app.quit()

QTimer.singleShot(4500, grab)  # a few 1 s poll cycles first
app.exec()
```

Run scripts from the scratchpad with plain `python script.py` (never a
PowerShell `python -c` here-string — Windows arg quoting mangles it).

## Gotchas

- `MainWindow.__init__` fetches `/schema` synchronously — with no server
  reachable the GUI **crashes at construction**; a dead-server probe must
  target mid-session death (kill the sim server under a running GUI).
- Console prints of `°` show as `�` (codepage); trust the screenshot.
- This FastAPI stack serializes NaN/Inf → null itself; a plain
  `json.dumps(allow_nan=False)` would raise. `server.app._json_value`
  converts explicitly so behavior is stack-independent.
