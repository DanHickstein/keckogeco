"""REST client + Qt polling thread for the engineering GUI.

The GUI never touches hardware: COM ports are exclusive and the server
must run 24/7 for the KTL dispatcher, so the GUI is a client like any
other. A crash here cannot drop instrument connections, and the GUI can
run from any machine that reaches the server.
"""

from __future__ import annotations

import logging

import requests
from PyQt6.QtCore import QThread, pyqtSignal

__all__ = ["KeckogecoClient", "PollThread", "WriteThread"]

log = logging.getLogger(__name__)


class KeckogecoClient:
    """Thin wrapper over the server's REST API."""

    def __init__(
        self, base_url: str = "http://localhost:8000", token: str = "", timeout: float = 10.0
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"

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
            timeout=max(self.timeout, 30.0),  # ramped writes can be slow
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

    def arrays(self) -> list[str]:
        return self._get("arrays")["arrays"]

    def array(self, name: str) -> dict:
        return self._get(f"arrays/{name}")


class PollThread(QThread):
    """Polls /keywords and /state, emitting fresh data as Qt signals."""

    keywords_ready = pyqtSignal(dict)
    state_ready = pyqtSignal(dict)
    arrays_available = pyqtSignal(list)
    array_ready = pyqtSignal(str, dict)
    connection_changed = pyqtSignal(bool, str)

    #: arrays are fetched every Nth poll cycle (spectra sweeps are slow)
    ARRAY_EVERY = 3

    def __init__(self, client: KeckogecoClient, period_ms: int = 1000):
        super().__init__()
        self.client = client
        self.period_ms = period_ms
        self.array_names: list[str] = []  # set by the main window
        self._running = True
        self._connected = None
        self._cycle = 0

    def run(self) -> None:
        while self._running:
            try:
                self.keywords_ready.emit(self.client.snapshot())
                self.state_ready.emit(self.client.state())
                self._set_connected(True, "")
                if self._cycle % self.ARRAY_EVERY == 0:
                    # what the server offers can change (a restart brings the
                    # OSA online); the main window subscribes via array_names
                    try:
                        self.arrays_available.emit(self.client.arrays())
                    except Exception as exc:  # noqa: BLE001 - older server
                        log.debug("arrays list fetch failed: %s", exc)
                    for name in list(self.array_names):
                        try:
                            self.array_ready.emit(name, self.client.array(name))
                        except Exception as exc:  # noqa: BLE001 - one bad array is fine
                            log.debug("array %s fetch failed: %s", name, exc)
            except Exception as exc:  # noqa: BLE001 - report and keep polling
                self._set_connected(False, str(exc))
            self._cycle += 1
            self.msleep(self.period_ms)

    def _set_connected(self, ok: bool, detail: str) -> None:
        if ok != self._connected:
            self._connected = ok
            self.connection_changed.emit(ok, detail)

    def stop(self) -> None:
        self._running = False
        self.wait(3000)


class WriteThread(QThread):
    """Serializes keyword writes off the GUI thread."""

    write_ok = pyqtSignal(str, object)
    write_failed = pyqtSignal(str, str)

    def __init__(self, client: KeckogecoClient):
        super().__init__()
        self.client = client
        self._queue: list[tuple[str, object]] = []
        self._running = True

    def submit(self, name: str, value) -> None:
        self._queue.append((name, value))

    def run(self) -> None:
        while self._running:
            if self._queue:
                name, value = self._queue.pop(0)
                try:
                    result = self.client.write(name, value)
                    self.write_ok.emit(name, result.get("value"))
                except Exception as exc:  # noqa: BLE001
                    self.write_failed.emit(name, str(exc))
            else:
                self.msleep(50)

    def stop(self) -> None:
        self._running = False
        self.wait(3000)
