"""REST client + Qt polling thread for the engineering GUI.

The GUI never touches hardware: COM ports are exclusive and the server
must run 24/7 for the KTL dispatcher, so the GUI is a client like any
other. A crash here cannot drop instrument connections, and the GUI can
run from any machine that reaches the server.
"""

from __future__ import annotations

import logging
import re
import threading

import requests
from PyQt6.QtCore import QThread, pyqtSignal

__all__ = ["ArrayPollThread", "KeckogecoClient", "PollThread", "WriteThread"]

log = logging.getLogger(__name__)


class KeckogecoClient:
    """Thin wrapper over the server's REST API."""

    def __init__(
        self, base_url: str = "http://127.0.0.1:8000", token: str = "", timeout: float = 10.0
    ):
        # "localhost" resolves to ::1 first on the laptop and the server
        # listens on IPv4 only, so every NEW connection burned ~2 s in the
        # IPv6 connect timeout before falling back (measured 2026-07-21:
        # 2071 ms via localhost vs 3 ms via 127.0.0.1). The server is
        # always IPv4, so the spelling is safe to normalize away.
        self.base_url = re.sub(
            r"^(https?://)localhost([:/]|$)", r"\g<1>127.0.0.1\2", base_url
        ).rstrip("/")
        self.token = token
        self.timeout = timeout
        self.session = requests.Session()
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"

    def clone(self) -> KeckogecoClient:
        """A new client (own requests.Session/connection) for the same
        server — one per thread, so a slow transfer on one thread never
        queues behind another's connection."""
        return KeckogecoClient(self.base_url, token=self.token, timeout=self.timeout)

    def _get(self, path: str, **params) -> dict:
        response = self.session.get(
            f"{self.base_url}/api/v1/{path}", params=params, timeout=self.timeout
        )
        response.raise_for_status()
        return response.json()

    def health(self) -> dict:
        return self._get("health")

    def schema(self) -> dict:
        return self._get("schema")

    def snapshot(self) -> dict:
        return self._get("keywords")

    def state(self) -> dict:
        return self._get("state")

    def read(self, name: str, fresh: bool = False) -> dict:
        return self._get(f"keywords/{name}", fresh=int(fresh))

    def write(self, name: str, value) -> dict:
        response = self.session.put(
            f"{self.base_url}/api/v1/keywords/{name}",
            json={"value": value},
            # ramped writes can be slow: the Pritel's 0 -> 3.9 A power-amp
            # ramp is ~20 steps with a 1 s dwell (~35 s; was ~1 min before
            # the 2026-07-18 step change). A shorter timeout made the GUI
            # report WRITE FAILED on a write that then succeeded.
            timeout=max(self.timeout, 120.0),
        )
        if response.status_code >= 400:
            detail = response.json().get("detail", response.text)
            raise RuntimeError(f"{name}: {detail}")
        return response.json()

    def start_action(self, name: str) -> dict:
        response = self.session.post(f"{self.base_url}/api/v1/actions/{name}", timeout=self.timeout)
        if response.status_code >= 400:
            raise RuntimeError(response.json().get("detail", response.text))
        return response.json()

    def abort_action(self) -> dict:
        response = self.session.delete(
            f"{self.base_url}/api/v1/actions/current", timeout=self.timeout
        )
        return response.json()

    def devices(self) -> dict:
        return self._get("devices")

    def interlock(self) -> dict:
        return self._get("interlock")

    def arrays(self) -> list[str]:
        return self._get("arrays")["arrays"]

    def array(self, name: str) -> dict:
        return self._get(f"arrays/{name}")

    def osa_settings(self) -> dict:
        return self._get("osa")

    def osa_apply(self, **settings) -> dict:
        """Write the given OSA settings (start_nm, stop_nm, resolution_nm,
        sensitivity_dBm); returns the read-back settings."""
        response = self.session.put(
            f"{self.base_url}/api/v1/osa", json=settings, timeout=self.timeout
        )
        if response.status_code >= 400:
            raise RuntimeError(response.json().get("detail", response.text))
        return response.json()

    def im_apply(self, **settings) -> dict:
        """Write IM servo settings (setpoint_V, prop_gain, intg_gain);
        returns the read-back servo state (mode, setpoint_V, bias_V,
        input_V, prop_gain, intg_gain). Call with no settings to read."""
        response = self.session.put(
            f"{self.base_url}/api/v1/im", json=settings, timeout=self.timeout
        )
        if response.status_code >= 400:
            raise RuntimeError(response.json().get("detail", response.text))
        return response.json()

    def im_scan(self, **params) -> dict:
        """Start an IM bias scan (v_start, v_stop, v_step, settle_s);
        progress comes back through /state and the im_scan array."""
        response = self.session.post(
            f"{self.base_url}/api/v1/im/scan", json=params, timeout=self.timeout
        )
        if response.status_code >= 400:
            raise RuntimeError(response.json().get("detail", response.text))
        return response.json()

    def flattener_slider(self) -> dict:
        """Read the ND-filter slider state (position, positions)."""
        return self._get("flattener/slider")

    def flattener_slider_set(self, position: int) -> dict:
        """Move the ND-filter slider to a slot; returns the read-back."""
        response = self.session.put(
            f"{self.base_url}/api/v1/flattener/slider",
            json={"position": position},
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            raise RuntimeError(response.json().get("detail", response.text))
        return response.json()

    def flattener_slider_home(self) -> dict:
        """Home the ND-filter slider; returns the read-back."""
        response = self.session.post(
            f"{self.base_url}/api/v1/flattener/slider/home", timeout=self.timeout
        )
        if response.status_code >= 400:
            raise RuntimeError(response.json().get("detail", response.text))
        return response.json()

    def osa_sweep(self, mode: str) -> dict:
        response = self.session.post(
            f"{self.base_url}/api/v1/osa/sweep", json={"mode": mode}, timeout=self.timeout
        )
        if response.status_code >= 400:
            raise RuntimeError(response.json().get("detail", response.text))
        return response.json()


class PollThread(QThread):
    """Polls /keywords and /state, emitting fresh data as Qt signals.

    Arrays (spectra) live on :class:`ArrayPollThread`: an OSA trace
    transfer used to sit in this loop and stall the status/keyword
    refresh behind it every third cycle."""

    keywords_ready = pyqtSignal(dict)
    state_ready = pyqtSignal(dict)
    connection_changed = pyqtSignal(bool, str)

    def __init__(self, client: KeckogecoClient, period_ms: int = 1000):
        super().__init__()
        self.client = client
        self.period_ms = period_ms
        self._running = True
        self._connected = None

    def run(self) -> None:
        while self._running:
            try:
                self.keywords_ready.emit(self.client.snapshot())
                self.state_ready.emit(self.client.state())
                self._set_connected(True, "")
            except Exception as exc:  # noqa: BLE001 - report and keep polling
                self._set_connected(False, str(exc))
            self.msleep(self.period_ms)

    def _set_connected(self, ok: bool, detail: str) -> None:
        if ok != self._connected:
            self._connected = ok
            self.connection_changed.emit(ok, detail)

    def stop(self) -> None:
        self._running = False
        self.wait(3000)


class ArrayPollThread(QThread):
    """Fetches the server's data arrays (OSA spectrum, IM scan) on their
    own thread and connection, so a slow trace transfer only delays the
    next trace — keywords and /state keep their own cadence on
    :class:`PollThread`. Subscribed arrays refresh every cycle; a slower
    per-array cadence can be set in ``array_every`` (in cycles)."""

    arrays_available = pyqtSignal(list)
    array_ready = pyqtSignal(str, dict)

    #: the /arrays listing (cheap, no hardware) is re-checked every Nth
    #: cycle — what the server offers can change (a restart brings the
    #: OSA online); the main window subscribes via ``array_names``
    LIST_EVERY = 3

    def __init__(self, client: KeckogecoClient, period_ms: int = 1000):
        super().__init__()
        self.client = client
        self.period_ms = period_ms
        self.array_names: list[str] = []  # set by the main window
        self.array_every: dict[str, int] = {}  # name -> cadence override
        self._running = True
        self._cycle = 0

    def run(self) -> None:
        while self._running:
            if self._cycle % self.LIST_EVERY == 0:
                try:
                    self.arrays_available.emit(self.client.arrays())
                except Exception as exc:  # noqa: BLE001 - server down/older
                    log.debug("arrays list fetch failed: %s", exc)
            for name in list(self.array_names):
                if self._cycle % self.array_every.get(name, 1):
                    continue
                try:
                    self.array_ready.emit(name, self.client.array(name))
                except Exception as exc:  # noqa: BLE001 - one bad array is fine
                    log.debug("array %s fetch failed: %s", name, exc)
            self._cycle += 1
            self.msleep(self.period_ms)

    def stop(self) -> None:
        self._running = False
        self.wait(3000)


class WriteThread(QThread):
    """Serializes keyword writes (and other client calls) off the GUI
    thread. ``submit`` queues a keyword write; ``submit_call`` queues an
    arbitrary client call (e.g. OSA settings) whose result comes back on
    ``call_done`` under the given label."""

    write_ok = pyqtSignal(str, object)
    write_failed = pyqtSignal(str, str)
    call_done = pyqtSignal(str, object)

    def __init__(self, client: KeckogecoClient):
        super().__init__()
        self.client = client
        self._queue: list[tuple] = []
        self._running = True

    def submit(self, name: str, value) -> None:
        self._queue.append(("write", name, value))

    def submit_urgent(self, name: str, value) -> None:
        """Run this write immediately on its own thread, jumping the FIFO
        queue. For power-off writes: an emergency stop must not wait
        behind a slow in-flight write (the Pritel's ~1 min power-amp
        ramp made Turn OFF appear to do nothing — 2026-07-18). Results
        come back on the same write_ok / write_failed signals."""

        def run() -> None:
            try:
                result = self.client.write(name, value)
                self.write_ok.emit(name, result.get("value"))
            except Exception as exc:  # noqa: BLE001
                self.write_failed.emit(name, str(exc))

        threading.Thread(target=run, name=f"urgent-write-{name}", daemon=True).start()

    def submit_call(self, label: str, func) -> None:
        """Queue ``func(client)``; its return value is emitted as
        ``call_done(label, result)`` (errors go to ``write_failed``)."""
        self._queue.append(("call", label, func))

    def run(self) -> None:
        while self._running:
            if self._queue:
                kind, name, payload = self._queue.pop(0)
                try:
                    if kind == "write":
                        result = self.client.write(name, payload)
                        self.write_ok.emit(name, result.get("value"))
                    else:
                        self.call_done.emit(name, payload(self.client))
                except Exception as exc:  # noqa: BLE001
                    self.write_failed.emit(name, str(exc))
            else:
                self.msleep(50)

    def stop(self) -> None:
        self._running = False
        self.wait(3000)
