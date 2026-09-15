"""Background monitors: heartbeat and telemetry logging.

Replaces the old unguarded ``test_clock`` thread (``KeckLFC.py:52``) and
the ad-hoc CSV loggers (``overnight_NIRSPEC_logging.py`` etc.). All cache
updates go through the registry, which is lock-protected.
"""

from __future__ import annotations

import csv
import logging
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

__all__ = [
    "Heartbeat",
    "InterlockChannel",
    "MonitorThread",
    "TelemetryLogger",
    "TempInterlock",
]

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
                if isinstance(value, list):
                    # arrays expand to one row per element, e.g.
                    # LFC_TEMP_TEST2[1] — the GUI-only DAQ thermocouples
                    # (RF amp, Pritel, ...) had no history before this
                    for i, element in enumerate(value):
                        writer.writerow([now, f"{name}[{i}]", element])
                else:
                    writer.writerow([now, name, value])


@dataclass(frozen=True)
class InterlockChannel:
    """One thermocouple the :class:`TempInterlock` watches."""

    name: str
    read: Callable[[], float]
    nominal_C: float


class TempInterlock(MonitorThread):
    """Over-temperature interlock for the glycol-cooled heat sources.

    Motivated by the 2026-08-15 glycol outage: if any watched thermocouple
    reads more than ``delta_C`` above its nominal value continuously for
    ``hold_s``, ``shutdown(reason)`` is called once and the interlock
    latches tripped. It re-arms by itself when every channel is back under
    its limit; while temperatures stay high the shutdown never re-fires,
    so an operator retains the authority to deliberately re-enable a
    device during an episode.

    A NaN reading (open thermocouple, failed read, DAQ offline) never
    trips — it resets that channel's timer. Only affirmative
    over-temperature readings count, the same policy as the DAQ driver's
    open-thermocouple NaN and the rep-rate monitor: a bad *reading* is
    reported, never acted on.
    """

    def __init__(
        self,
        channels: list[InterlockChannel],
        shutdown: Callable[[str], None],
        delta_C: float = 8.0,
        hold_s: float = 30.0,
        period_s: float = 5.0,
    ):
        super().__init__("temp-interlock", period_s, self._check)
        self.channels = list(channels)
        self.shutdown = shutdown
        self.delta_C = float(delta_C)
        self.hold_s = float(hold_s)
        self.tripped = False
        self.trip_reason: str | None = None
        self.tripped_at: str | None = None
        # the last trip's details, kept for the GUI message until an
        # operator acknowledges via clear_trip() — it survives the latch
        # auto re-arming, so an overnight shutdown still shows its cause
        self.last_trip: dict | None = None
        self.last_temps_C: dict[str, float] = {}
        self._over_since: dict[str, float] = {}

    def _check(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        temps: dict[str, float] = {}
        over: list[str] = []
        expired: list[InterlockChannel] = []
        expired_text: list[str] = []
        for ch in self.channels:
            try:
                temp = float(ch.read())
            except Exception as exc:  # noqa: BLE001 - one bad channel must not mask the rest
                log.warning("temp interlock: %s read failed: %s", ch.name, exc)
                temp = math.nan
            temps[ch.name] = temp
            limit = ch.nominal_C + self.delta_C
            if temp > limit:  # NaN compares False: bad readings never trip
                since = self._over_since.setdefault(ch.name, now)
                text = (
                    f"{ch.name} {temp:.1f} C > {limit:.1f} C limit "
                    f"(nominal {ch.nominal_C:.1f} + {self.delta_C:.0f}) "
                    f"for {now - since:.0f} s"
                )
                over.append(text)
                if now - since >= self.hold_s:
                    expired.append(ch)
                    expired_text.append(text)
            else:
                self._over_since.pop(ch.name, None)
        self.last_temps_C = temps
        if self.tripped and self.last_trip is not None:
            # the episode's peak keeps updating after the shutdown, so the
            # message reports the true maximum, not the temp at trip time
            for entry in self.last_trip["channels"]:
                if temps.get(entry["name"], math.nan) > entry["max_temp_C"]:
                    entry["max_temp_C"] = temps[entry["name"]]
        if self.tripped:
            if not over:
                self.tripped = False
                log.warning("temp interlock re-armed: all channels back under their limits")
        elif expired:
            self.trip_reason = "; ".join(expired_text)
            self.tripped_at = f"{datetime.now():%Y-%m-%d %H:%M:%S}"
            self.last_trip = {
                "at": self.tripped_at,
                "channels": [
                    {
                        "name": ch.name,
                        "max_temp_C": temps[ch.name],
                        "limit_C": ch.nominal_C + self.delta_C,
                    }
                    for ch in expired
                ],
            }
            self.tripped = True
            log.critical("TEMP INTERLOCK TRIPPED: %s", self.trip_reason)
            self.shutdown(self.trip_reason)
        elif over:
            log.warning("temp interlock: %s (shutdown after %.0f s)", "; ".join(over), self.hold_s)

    def clear_trip(self) -> None:
        """Operator acknowledgement (the Pritel or the RF amplifier was
        turned back on): drop the trip message and start over. If a
        channel is still over its limit a fresh ``hold_s`` countdown
        begins, so the shutdown can fire again."""
        if self.tripped or self.last_trip is not None:
            log.warning("temp interlock trip cleared by operator")
        self.tripped = False
        self.trip_reason = None
        self.tripped_at = None
        self.last_trip = None
        self._over_since.clear()

    def info(self) -> dict:
        """Status for ``/state`` (non-finite temps go out as None/null)."""
        now = time.monotonic()
        last_trip = self.last_trip
        if last_trip is not None:  # copy: _check mutates the peak in place
            last_trip = {
                "at": last_trip["at"],
                "channels": [dict(c) for c in last_trip["channels"]],
            }
        return {
            "tripped": self.tripped,
            "trip_reason": self.trip_reason,
            "tripped_at": self.tripped_at,
            "delta_C": self.delta_C,
            "hold_s": self.hold_s,
            "last_trip": last_trip,
            "channels": [
                {
                    "name": ch.name,
                    "nominal_C": ch.nominal_C,
                    "limit_C": ch.nominal_C + self.delta_C,
                    "temperature_C": (
                        temp
                        if (temp := self.last_temps_C.get(ch.name)) is not None
                        and math.isfinite(temp)
                        else None
                    ),
                    # seconds this channel has been over its limit (None
                    # when under); the GUI's countdown is hold_s - over_s
                    "over_s": (
                        now - since
                        if (since := self._over_since.get(ch.name)) is not None
                        else None
                    ),
                }
                for ch in self.channels
            ],
        }


def read_telemetry(directory: str | Path, date: str | None = None):
    """Load one day's telemetry as a pandas DataFrame (helper for analysis)."""
    import pandas as pd

    directory = Path(directory)
    date = date or f"{datetime.now():%Y-%m-%d}"
    return pd.read_csv(directory / f"{date}.csv", parse_dates=["timestamp"])
