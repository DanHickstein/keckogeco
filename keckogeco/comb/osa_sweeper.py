"""Single-sweep acquisition manager for the server's OSA.

The OSA is never put into continuous sweep (Dan, 2026-07-29): the server
triggers one sweep at a time, so a crash — server, GUI, or laptop —
leaves the monochromator parked instead of sweeping unattended forever.

Two cadences, selected via ``POST /osa/sweep``:

- **fast**: grabs back-to-back (one sweep is ~1 s at the commissioned
  mini-comb view), the live-view mode while someone is watching. It
  drops to slow after ``FAST_IDLE_TIMEOUT_S`` without a ``touch()`` —
  the GUI renews on real user input, so a dead GUI can't pin the OSA
  in fast mode.
- **slow**: one grab every ``SLOW_PERIOD_S`` — the unattended default.

``/arrays/osa_spectrum`` serves the newest grab from cache, so array
polls no longer cost a GPIB transfer per request. Every
``LOG_PERIOD_S`` the newest spectrum is written to
``logs/spectra/<date>/`` (mini-comb history; format shared with the
GUI Save button, so ``scripts/plot_spectra.py`` reads them directly).

The Yokogawa standalone GUI implements the same fast/slow/idle scheme
against these constants (``keckogeco/gui/yokogawa_app.py``).
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from ..spectra import save_spectrum_csv

__all__ = ["FAST_IDLE_TIMEOUT_S", "LOG_PERIOD_S", "SLOW_PERIOD_S", "OsaSweeper"]

log = logging.getLogger(__name__)

#: seconds of no user interaction before fast drops to slow
FAST_IDLE_TIMEOUT_S = 30 * 60.0
#: seconds between grabs in slow mode
SLOW_PERIOD_S = 10 * 60.0
#: seconds between spectrum log files
LOG_PERIOD_S = 10 * 60.0


class OsaSweeper(threading.Thread):
    """Owns the OSA sweep cadence; all grabs go through the driver's
    own RLock, so API settings reads/writes interleave safely."""

    MODES = ("fast", "slow")
    #: wait after a failed grab before trying again (a wedged instrument
    #: must not be hammered — see the Amonics reconnect storm, AGENTS.md)
    RETRY_S = 10.0

    def __init__(
        self,
        osa,
        log_dir: str | Path | None = None,
        *,
        fast_idle_timeout_s: float = FAST_IDLE_TIMEOUT_S,
        slow_period_s: float = SLOW_PERIOD_S,
        log_period_s: float = LOG_PERIOD_S,
    ):
        super().__init__(name="osa-sweeper", daemon=True)
        self._osa = osa
        self._log_dir = Path(log_dir) if log_dir else None
        self.fast_idle_timeout_s = fast_idle_timeout_s
        self.slow_period_s = slow_period_s
        self.log_period_s = log_period_s
        self._mode = "slow"
        self._fast_deadline = 0.0
        self._cache: dict | None = None
        self._cache_lock = threading.Lock()
        self._grab_serial = 0  # bumps per successful grab; gates re-logging
        self._logged_serial = 0
        self._last_grab: float | None = None  # monotonic
        self._last_log: float | None = None
        self._grab_requested = False
        self._retry_at = 0.0
        self._wake = threading.Event()
        self._stop_event = threading.Event()

    # ------------------------------------------------------------- API side

    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        if mode not in self.MODES:
            raise ValueError(f"sweep mode must be one of {self.MODES}, got {mode!r}")
        self._mode = mode
        if mode == "fast":
            self._fast_deadline = time.monotonic() + self.fast_idle_timeout_s
        self.request_grab()  # either button means "show me a spectrum now"

    def touch(self) -> None:
        """User interaction: keep fast mode alive another idle period."""
        if self._mode == "fast":
            self._fast_deadline = time.monotonic() + self.fast_idle_timeout_s

    def request_grab(self) -> None:
        """Grab at the next opportunity regardless of cadence (e.g. right
        after a settings change, so the new view appears promptly)."""
        self._grab_requested = True
        self._retry_at = 0.0
        self._wake.set()

    def latest(self) -> dict:
        """Newest grabbed spectrum (the /arrays payload). Before the first
        background grab lands, grabs synchronously so early clients (and
        tests) never see an empty cache."""
        with self._cache_lock:
            cache = self._cache
        if cache is None:
            self._grab_once()
            with self._cache_lock:
                cache = self._cache
        return dict(cache)

    def info(self) -> dict:
        """Sweep-manager state, merged into GET /osa and /state."""
        with self._cache_lock:
            spectrum_time = self._cache["time"] if self._cache else None
        remaining = self._fast_deadline - time.monotonic()
        return {
            "sweep_mode": self._mode,
            "spectrum_time": spectrum_time,
            "fast_remaining_s": round(max(remaining, 0.0)) if self._mode == "fast" else None,
        }

    def stop(self) -> None:
        self._stop_event.set()
        self._wake.set()

    # ---------------------------------------------------------- worker side

    def _grab_once(self) -> None:
        wavelength, power = self._osa.grab_single()
        payload = {
            "x": wavelength.tolist(),
            "y": power.tolist(),
            "x_label": "wavelength (nm)",
            "y_label": "power (dBm)",
            "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        with self._cache_lock:
            self._cache = payload
            self._grab_serial += 1
        self._last_grab = time.monotonic()

    def _maybe_log(self) -> None:
        if self._log_dir is None:
            return
        now = time.monotonic()
        if self._last_log is not None and now - self._last_log < self.log_period_s:
            return
        with self._cache_lock:
            cache = self._cache
            serial = self._grab_serial
        if cache is None or serial == self._logged_serial:
            return  # nothing new since the last file — don't re-log stale data
        metadata = {"instrument": "agilent_86142b", "points": len(cache["x"])}
        try:
            metadata.update(self._osa.status())
        except Exception as exc:  # noqa: BLE001 - settings are nice-to-have
            log.debug("OSA status for spectrum log failed: %s", exc)
        day_dir = self._log_dir / time.strftime("%Y-%m-%d")
        day_dir.mkdir(parents=True, exist_ok=True)
        path = day_dir / time.strftime("osa_%H%M%S.csv")
        save_spectrum_csv(path, cache["x"], cache["y"], metadata)
        self._last_log = now
        self._logged_serial = serial
        log.info("OSA spectrum logged to %s", path)

    def run(self) -> None:
        while not self._stop_event.is_set():
            now = time.monotonic()
            if self._mode == "fast" and now >= self._fast_deadline:
                self._mode = "slow"
                log.info(
                    "OSA fast sweeping idle for %.0f min - dropping to one sweep per %.0f min",
                    self.fast_idle_timeout_s / 60,
                    self.slow_period_s / 60,
                )
            due = (
                self._grab_requested
                or self._mode == "fast"
                or self._last_grab is None
                or now - self._last_grab >= self.slow_period_s
            )
            if due and now >= self._retry_at:
                self._grab_requested = False
                try:
                    self._grab_once()
                except Exception as exc:  # noqa: BLE001 - keep the cadence alive
                    log.warning("OSA single-sweep grab failed: %s", exc)
                    self._retry_at = time.monotonic() + self.RETRY_S
            try:
                self._maybe_log()
            except Exception as exc:  # noqa: BLE001 - logging must not stop grabs
                log.warning("OSA spectrum log failed: %s", exc)
            if self._mode == "fast" and self._retry_at <= time.monotonic():
                wait_s = 0.02  # grabs back-to-back; the sweep itself paces us
            else:
                next_grab = max((self._last_grab or 0.0) + self.slow_period_s, self._retry_at)
                wait_s = min(max(next_grab - time.monotonic(), 0.05), 5.0)
            if self._wake.wait(timeout=wait_s):
                self._wake.clear()
