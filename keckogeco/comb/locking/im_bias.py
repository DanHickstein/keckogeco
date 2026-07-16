"""Intensity-modulator bias scan + lock-point recommendation.

Locking itself is deliberately manual (simplification with Dan,
2026-07-15, superseding both the ported ``LFC_IM_AUTO_LOCK`` sweep and
the short-lived saved-lockpoint auto-engage): the operator enters the
photodiode setpoint, the starting bias, and the PI gains in the GUI's
servo panel and presses Lock (``LFC_IM_LOCK_MODE`` — engaging copies
the manual bias into the SIM960 output offset so the PID starts from
there). This module only provides the measurement side:

* :func:`im_bias_scan` sweeps the bias and records the photodiode
  response (the ``im_bias_scan`` action, plotted live by the GUI);
* :func:`recommend_lock_point` turns a finished scan into suggested
  numbers for the panel — mid-fringe bias, the photodiode voltage
  there, and PI gains scaled from the measured slope.
"""

from __future__ import annotations

import logging
import time

import numpy as np

__all__ = ["im_bias_scan", "recommend_lock_point"]

log = logging.getLogger(__name__)

#: hardware/software bias limit: ±8 V operating limit, under the
#: SIM960's ±10 V output spec (Dan, 2026-07-15)
BIAS_LIMIT_V = 8.0

#: commissioned loop tuning (live on the rack 2026-07-15: P −2.0 with a
#: slope of −1 V/V, I 0.1): the P recommendation keeps |P × slope| at
#: this loop gain, the I recommendation is used as-is
LOOP_GAIN = 2.0
INTG_GAIN = 0.1


def im_bias_scan(
    servo,
    v_start: float = -2.0,
    v_stop: float = 1.0,
    v_step: float = 0.02,
    settle_s: float = 0.2,
    sim: bool = False,
    point=None,
) -> tuple[np.ndarray, np.ndarray]:
    """Sweep the servo's manual bias output, reading the measured input
    (the minicomb photodiode) at each step. Returns (voltages, inputs).

    Puts the servo in manual mode and leaves the output at the last sweep
    point; callers decide the operating point afterwards (the GUI scan
    action restores the pre-scan bias).

    Parameters
    ----------
    servo : keckogeco.drivers.srs_sim900.SIM960
    point : callable(index, bias_V, input_V) | None
        Called after each measurement (live plotting / abort checks).
    """
    voltages = np.arange(v_start, v_stop, v_step)
    if len(voltages) < 2:
        raise ValueError(
            f"scan range {v_start}..{v_stop} V in {v_step} V steps has "
            f"{len(voltages)} point(s); need at least 2"
        )
    servo.output_mode = "MAN"
    inputs = np.empty(len(voltages))
    for i, volt in enumerate(voltages):
        servo.manual_output_V = float(volt)
        if not sim:
            time.sleep(settle_s)
        inputs[i] = servo.measure_input_V
        if point is not None:
            point(i, float(volt), float(inputs[i]))
    return voltages, inputs


def recommend_lock_point(voltages, inputs, near_bias: float | None = None) -> dict:
    """Suggest lock settings from a completed bias scan (pure math, no
    hardware — the GUI runs this on the ``im_scan`` array).

    Mid-fringe selection is the old commissioned procedure's: a bias
    whose photodiode reading is closest to the (max+min)/2 target — the
    steepest, most linear part of the fringe. A wide scan crosses that
    target on several fringes; ``near_bias`` (the operator's lock-start
    bias) picks the crossing closest to it, so the suggestion stays on
    the fringe the lock already uses. The PI suggestion scales the
    proportional gain off the local slope so the loop gain matches the
    commissioned tuning (P = LOOP_GAIN / slope, sign included: the
    SIM960's P must carry the plant slope's sign), with the commissioned
    integral gain.

    Returns ``bias_V``, ``setpoint_V``, ``slope_V_per_V``, ``prop_gain``,
    ``intg_gain``, plus the sweep's ``input_min_V``/``input_max_V``.

    Raises
    ------
    ValueError
        Flat scan (no modulation) or no usable slope at the midpoint.
    """
    voltages = np.asarray(voltages, dtype=float)
    inputs = np.asarray(inputs, dtype=float)
    if len(voltages) < 3 or len(voltages) != len(inputs):
        raise ValueError("need a completed scan of at least 3 points")
    idx_max, idx_min = int(np.argmax(inputs)), int(np.argmin(inputs))
    if inputs[idx_max] == inputs[idx_min]:
        raise ValueError(
            "scan shows no modulation (flat response); check the RF drive and photodiode"
        )
    target = (inputs[idx_max] + inputs[idx_min]) / 2
    diffs = inputs - target
    best = int(np.argmin(np.abs(diffs)))
    if near_bias is not None:
        # sign changes of (input - target) = the mid-fringe crossings
        crossings = np.nonzero(np.signbit(diffs[:-1]) != np.signbit(diffs[1:]))[0]
        if len(crossings):
            left = int(crossings[np.argmin(np.abs(voltages[crossings] - near_bias))])
            best = left if abs(diffs[left]) <= abs(diffs[left + 1]) else left + 1
    best = min(max(best, 1), len(voltages) - 2)  # keep neighbors for the slope
    slope = float(np.gradient(inputs, voltages)[best])
    if slope == 0:
        raise ValueError(
            "no slope at the mid-fringe point; re-scan with a finer step or larger range"
        )
    return {
        "bias_V": float(voltages[best]),
        "setpoint_V": float(inputs[best]),
        "slope_V_per_V": slope,
        "prop_gain": round(LOOP_GAIN / slope, 1),  # SIM960 GAIN resolution is 0.1
        "intg_gain": INTG_GAIN,
        "input_min_V": float(inputs[idx_min]),
        "input_max_V": float(inputs[idx_max]),
    }
