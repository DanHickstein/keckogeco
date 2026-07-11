"""Background monitors: heartbeat and telemetry logging.

Replaces the old unguarded ``test_clock`` thread (``KeckLFC.py:52``) and
the ad-hoc CSV loggers (``overnight_NIRSPEC_logging.py`` etc.). All cache
updates go through the registry, which is lock-protected.
"""

from __future__ import annotations

import csv
import logging
import threading
import time
from datetime import datetime
from pathlib import Path

__all__ = ["Heartbeat", "MonitorThread", "TelemetryLogger"]

log = logging.getLogger(__name__)


class MonitorThread(threading.Thread):
    """Run ``fn()`` every ``period_s`` until stopped; errors are logged,
    never fatal."""

    def __init__(self, name: str, period_s: float, fn):
        super().__init__(name=f"monitor-{name}", daemon=True)
        self.monitor_name = name
        self.period_s = period_s
        self.fn = fn
        self._stop_event = threading.Event()
        self.enabled = True

    def run(self) -> None:
        while not self._stop_event.is_set():
            if self.enabled:
                try:
                    self.fn()
                except Exception as exc:  # noqa: BLE001 - monitors must survive
                    log.warning("monitor %s: %s", self.monitor_name, exc)
            self._stop_event.wait(self.period_s)

    def stop(self) -> None:
        self._stop_event.set()


class Heartbeat(MonitorThread):
    """Pokes the ICECLK keyword with epoch seconds so the KTL side (and
    anything watching the API) can see the server is alive."""

    def __init__(self, registry, period_s: float = 1.0):
        super().__init__("heartbeat", period_s, self._beat)
        self.registry = registry

    def _beat(self) -> None:
        self.registry.poke("ICECLK", int(time.time()))


class TelemetryLogger(MonitorThread):
    """Appends the registry cache to a daily CSV in long format.

    Long format (``timestamp, keyword, value``) instead of one column per
    keyword: the set of live keywords changes as devices come and go, and
    long format stays greppable and trivially pivotable in pandas::

        import pandas as pd
        df = pd.read_csv("logs/telemetry/2026-07-11.csv")
        df.pivot_table(index="timestamp", columns="keyword", values="value")
    """

    def __init__(self, registry, directory: str | Path, period_s: float = 30.0):
        super().__init__("telemetry", period_s, self._log_row)
        self.registry = registry
        self.directory = Path(directory)

    def _log_row(self) -> None:
        snapshot = self.registry.snapshot()
        if not snapshot:
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{datetime.now():%Y-%m-%d}.csv"
        new_file = not path.exists()
        now = f"{datetime.now():%Y-%m-%d %H:%M:%S}"
        with open(path, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if new_file:
                writer.writerow(["timestamp", "keyword", "value"])
            for name in sorted(snapshot):
                value = snapshot[name].value
                if isinstance(value, list):  # arrays don't belong in telemetry
                    continue
                writer.writerow([now, name, value])


def read_telemetry(directory: str | Path, date: str | None = None):
    """Load one day's telemetry as a pandas DataFrame (helper for analysis)."""
    import pandas as pd

    directory = Path(directory)
    date = date or f"{datetime.now():%Y-%m-%d}"
    return pd.read_csv(directory / f"{date}.csv", parse_dates=["timestamp"])
