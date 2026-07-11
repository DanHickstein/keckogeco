"""Comb state model.

Replaces the product-of-primes encoding in the old ``LFC_CHECK_STATUS``
(``KeckLFC.py:2119``) with an explicit enum and a pure, unit-testable
``evaluate``. The legacy code (30030 = FULL COMB, 15015 = STANDBY, 1 = OFF)
is still computed by :func:`legacy_code` because the deployed KTL keyword
``LFC_CHECK_STATUS`` reports it; ``ktl/keyword-changes.md`` proposes an
enumerated replacement.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

__all__ = ["PRIMES", "CombState", "SubsystemStatus", "evaluate", "legacy_code"]


class CombState(Enum):
    OFF = "OFF"
    STANDBY = "STANDBY"
    FULL_COMB = "FULL COMB"
    TRANSITIONING = "TRANSITIONING"
    FAULT = "FAULT"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class SubsystemStatus:
    """On/off snapshot of the six subsystems the old state check probed.

    ``None`` means "could not be determined" (device offline).
    """

    ptamp_on: bool | None = None
    edfa23_on: bool | None = None
    edfa27_on: bool | None = None
    rf_oscillator_on: bool | None = None
    rf_amplifier_on: bool | None = None
    rep_rate_detected: bool | None = None


# Subsystem -> prime factor, as in the old LFC_CHECK_STATUS.
PRIMES = {
    "ptamp_on": 2,
    "edfa23_on": 3,
    "edfa27_on": 5,
    "rf_oscillator_on": 7,
    "rf_amplifier_on": 11,
    "rep_rate_detected": 13,
}

_FULL = 2 * 3 * 5 * 7 * 11 * 13  # 30030
_STANDBY = _FULL // 2  # 15015: everything but the Pritel pump


def legacy_code(status: SubsystemStatus) -> int:
    """The old prime-product status code (unknown subsystems count as off)."""
    code = 1
    for field_name, prime in PRIMES.items():
        if getattr(status, field_name):
            code *= prime
    return code


def evaluate(status: SubsystemStatus) -> CombState:
    """Classify the comb state from a subsystem snapshot."""
    if any(getattr(status, field_name) is None for field_name in PRIMES):
        return CombState.UNKNOWN
    code = legacy_code(status)
    if code == _FULL:
        return CombState.FULL_COMB
    if code == _STANDBY:
        return CombState.STANDBY
    if code == 1:
        return CombState.OFF
    return CombState.FAULT
