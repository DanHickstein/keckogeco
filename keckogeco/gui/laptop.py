"""Health sensors for the laptop the GUI runs on (LAPTOP-LFC2).

The comb laptop sits closed-lid in a warm rack room and physical access
is rare, so the GUI's Laptop tab watches it for overheating. This is
deliberately GUI-local rather than a server endpoint: the sources are
shareable Windows performance counters (no COM port, no exclusivity),
and the laptop's own health must stay visible even while the server is
down.

Everything here is limited to what a **non-admin** user can read (no
admin rights on LAPTOP-LFC2; probed 2026-07-17):

- ACPI thermal-zone temperature, throttle state, and passive cooling
  limit via the ``Thermal Zone Information`` performance counters (PDH).
  The richer paths — ``MSAcpi_ThermalZoneTemperature`` WMI, NVMe SMART
  temperature, vendor EC fan RPM — are all access-denied without admin.
- CPU load and effective clock via ``Processor Information``.
- Battery / AC state via ``GetSystemPowerStatus``.

Fan RPM is therefore **not** available. The fan-health proxy is
Windows' own thermal response: a zone's "% Passive Limit" dropping
below 100 or a nonzero "Throttle Reasons" means Windows is slowing the
machine down because cooling can't keep up — which is also the moment
overheating starts to matter.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes
from dataclasses import dataclass, field

from PyQt6.QtCore import QThread, pyqtSignal

__all__ = [
    "TEMP_HOT_C",
    "TEMP_WARN_C",
    "LaptopHealthError",
    "LaptopHealthReader",
    "LaptopPollThread",
    "LaptopSample",
    "temp_state",
    "zone_label",
]

log = logging.getLogger(__name__)

#: absolute zone-temperature bands for the readout coloring. The ThinkPad's
#: CPU zone (THM0) idles around 75 C with the lid closed; mobile Intel parts
#: throttle themselves near 100 C, so 85+ is "warmer than it should ever
#: idle" and 95+ is "at the edge of self-protection".
TEMP_WARN_C = 85.0
TEMP_HOT_C = 95.0


def temp_state(temp_C: float) -> str:
    """Classify a zone temperature: ``"ok"``, ``"warn"`` or ``"hot"``."""
    if temp_C >= TEMP_HOT_C:
        return "hot"
    if temp_C >= TEMP_WARN_C:
        return "warn"
    return "ok"


def zone_label(instance: str) -> str:
    """Short display name for a PDH thermal-zone instance.

    The counters name zones by ACPI path (``\\_TZ.THM0``); the last
    path component is the zone's own name.
    """
    name = instance.rsplit(".", 1)[-1].lstrip("\\_")
    return name or instance


@dataclass
class LaptopSample:
    """One reading of the laptop's health sensors.

    Zone dicts are keyed by the short zone name (``THM0``); all three
    share keys. ``None`` scalars mean the counter had no valid data yet
    (CPU rates need two collections) or the machine has no battery.
    """

    zones_C: dict[str, float] = field(default_factory=dict)
    throttle: dict[str, float] = field(default_factory=dict)
    passive_limit_pct: dict[str, float] = field(default_factory=dict)
    cpu_util_pct: float | None = None
    cpu_perf_pct: float | None = None
    ac_power: bool | None = None
    battery_pct: float | None = None
    error: str = ""

    @property
    def throttling(self) -> bool:
        """True when Windows is actively limiting performance for any
        zone — the closest thing to a "cooling is losing" flag we can
        read without admin."""
        return any(self.throttle.values()) or any(
            v < 100.0 for v in self.passive_limit_pct.values()
        )


class LaptopHealthError(RuntimeError):
    """The health counters cannot be read on this platform."""


# --- PDH (Performance Data Helper) plumbing ---------------------------------

_PDH_FMT_DOUBLE = 0x00000200
_PDH_MORE_DATA = 0x800007D2
# CStatus values that carry a usable number (VALID_DATA / NEW_DATA)
_PDH_OK_STATUS = (0x0, 0x1)

_COUNTER_PATHS = {
    # high-precision variant reads tenths of Kelvin (3482 -> 75.05 C)
    "temp_hi_dK": r"\Thermal Zone Information(*)\High Precision Temperature",
    "temp_K": r"\Thermal Zone Information(*)\Temperature",
    "throttle": r"\Thermal Zone Information(*)\Throttle Reasons",
    "passive": r"\Thermal Zone Information(*)\% Passive Limit",
    "util": r"\Processor Information(_Total)\% Processor Utility",
    "perf": r"\Processor Information(_Total)\% Processor Performance",
}


class _PDH_FMT_COUNTERVALUE(ctypes.Structure):
    _fields_ = [("CStatus", wintypes.DWORD), ("doubleValue", ctypes.c_double)]


class _PDH_FMT_COUNTERVALUE_ITEM_W(ctypes.Structure):
    _fields_ = [("szName", ctypes.c_wchar_p), ("FmtValue", _PDH_FMT_COUNTERVALUE)]


class _SYSTEM_POWER_STATUS(ctypes.Structure):
    _fields_ = [
        ("ACLineStatus", ctypes.c_ubyte),
        ("BatteryFlag", ctypes.c_ubyte),
        ("BatteryLifePercent", ctypes.c_ubyte),
        ("SystemStatusFlag", ctypes.c_ubyte),
        ("BatteryLifeTime", wintypes.DWORD),
        ("BatteryFullLifeTime", wintypes.DWORD),
    ]


class LaptopHealthReader:
    """Persistent PDH query over the health counters.

    One query handle is opened at construction and collected on every
    ``read()`` — the rate counters (CPU load/clock) only become valid
    from the second collection, so their first sample reads ``None``.
    """

    def __init__(self):
        if sys.platform != "win32":
            raise LaptopHealthError("laptop health counters are Windows-only")
        self._pdh = ctypes.windll.pdh
        self._kernel32 = ctypes.windll.kernel32
        self._query = wintypes.HANDLE()
        status = self._pdh.PdhOpenQueryW(None, 0, ctypes.byref(self._query))
        if status:
            raise LaptopHealthError(f"PdhOpenQuery failed: 0x{status & 0xFFFFFFFF:08x}")
        self._counters: dict[str, wintypes.HANDLE] = {}
        for name, path in _COUNTER_PATHS.items():
            handle = wintypes.HANDLE()
            status = self._pdh.PdhAddEnglishCounterW(self._query, path, 0, ctypes.byref(handle))
            if status:  # e.g. no thermal zones on a VM: skip, don't fail
                log.debug("counter %s unavailable: 0x%08x", path, status & 0xFFFFFFFF)
                continue
            self._counters[name] = handle
        # prime the rate counters so the second collect (first read) works
        self._pdh.PdhCollectQueryData(self._query)

    def read(self) -> LaptopSample:
        """Collect the query and return one sample (never raises)."""
        status = self._pdh.PdhCollectQueryData(self._query)
        if status:
            return LaptopSample(error=f"PdhCollectQueryData: 0x{status & 0xFFFFFFFF:08x}")
        zones: dict[str, float] = {}
        for instance, kelvin in self._array("temp_K").items():
            zones[zone_label(instance)] = kelvin - 273.15
        for instance, deci_kelvin in self._array("temp_hi_dK").items():
            zones[zone_label(instance)] = deci_kelvin / 10.0 - 273.15
        ac_power, battery_pct = self._power_status()
        return LaptopSample(
            zones_C=zones,
            throttle={zone_label(k): v for k, v in self._array("throttle").items()},
            passive_limit_pct={zone_label(k): v for k, v in self._array("passive").items()},
            cpu_util_pct=self._scalar("util"),
            cpu_perf_pct=self._scalar("perf"),
            ac_power=ac_power,
            battery_pct=battery_pct,
        )

    def close(self) -> None:
        if self._query:
            self._pdh.PdhCloseQuery(self._query)
            self._query = None

    # ------------------------------------------------------------- internals

    def _array(self, name: str) -> dict[str, float]:
        """All instances of a wildcard counter with valid data."""
        counter = self._counters.get(name)
        if counter is None:
            return {}
        size = wintypes.DWORD(0)
        count = wintypes.DWORD(0)
        status = self._pdh.PdhGetFormattedCounterArrayW(
            counter, _PDH_FMT_DOUBLE, ctypes.byref(size), ctypes.byref(count), None
        )
        if status & 0xFFFFFFFF != _PDH_MORE_DATA:
            return {}
        buffer = (ctypes.c_byte * size.value)()
        status = self._pdh.PdhGetFormattedCounterArrayW(
            counter, _PDH_FMT_DOUBLE, ctypes.byref(size), ctypes.byref(count), buffer
        )
        if status:
            return {}
        items = ctypes.cast(buffer, ctypes.POINTER(_PDH_FMT_COUNTERVALUE_ITEM_W))
        return {
            items[i].szName: items[i].FmtValue.doubleValue
            for i in range(count.value)
            if items[i].FmtValue.CStatus in _PDH_OK_STATUS
        }

    def _scalar(self, name: str) -> float | None:
        values = self._array(name)
        return next(iter(values.values()), None)

    def _power_status(self) -> tuple[bool | None, float | None]:
        status = _SYSTEM_POWER_STATUS()
        if not self._kernel32.GetSystemPowerStatus(ctypes.byref(status)):
            return None, None
        ac_power = {0: False, 1: True}.get(status.ACLineStatus)  # 255 = unknown
        battery_pct = None if status.BatteryLifePercent == 255 else float(status.BatteryLifePercent)
        return ac_power, battery_pct


class LaptopPollThread(QThread):
    """Samples the health counters off the GUI thread.

    On a platform without the counters it emits one sample carrying the
    reason in ``error`` and exits, so the tab explains itself.
    """

    sample_ready = pyqtSignal(object)

    def __init__(self, period_ms: int = 2000):
        super().__init__()
        self.period_ms = period_ms
        self._running = True

    def run(self) -> None:
        try:
            reader = LaptopHealthReader()
        except LaptopHealthError as exc:
            self.sample_ready.emit(LaptopSample(error=str(exc)))
            return
        try:
            while self._running:
                self.sample_ready.emit(reader.read())
                # sleep in short slices so stop() returns promptly
                for _ in range(max(1, self.period_ms // 100)):
                    if not self._running:
                        break
                    self.msleep(100)
        finally:
            reader.close()

    def stop(self) -> None:
        self._running = False
        self.wait(3000)
