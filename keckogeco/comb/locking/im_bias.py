"""Intensity-modulator bias auto-lock.

Port of ``LFC_IM_AUTO_LOCK`` (``KeckLFC.py:1976``), the commissioning
procedure that finds the IM transfer-function midpoint and hands over to
the SIM960 PID:

1. put the servo in manual mode with conservative gains and ±3 V limits,
2. sweep the manual output over [-2.0, 1.0] V in 20 mV steps, recording
   the measured input at each point,
3. the sweep's max/min slope sign sets the proportional-gain polarity,
4. the bias closest to the (max+min)/2 midpoint becomes the operating
   point: apply it, set the output offset there, and set the setpoint to
   the measured input,
5. switch the servo to PID mode.
"""

from __future__ import annotations

import logging
import time

import numpy as np

__all__ = ["im_auto_lock", "im_bias_scan"]

log = logging.getLogger(__name__)


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
    point; callers decide the operating point afterwards (the auto-lock
    moves to mid-fringe, the GUI scan restores the pre-scan bias).

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


def im_auto_lock(
    servo,
    v_start: float = -2.0,
    v_stop: float = 1.0,
    v_step: float = 0.02,
    settle_s: float = 0.2,
    prop_gain: float = 2.0,
    intg_gain: float = 0.1,
    output_limit_V: float = 3.0,
    sim: bool = False,
    progress=None,
) -> dict:
    """Run the IM bias lock on a SIM960 servo. Returns a result summary.

    Parameters
    ----------
    servo : keckogeco.drivers.srs_sim900.SIM960
    progress : callable(str) | None
        Called with a short message at each stage (action step reporting).
    """

    def report(message: str) -> None:
        log.info("im_auto_lock: %s", message)
        if progress is not None:
            progress(message)

    report("configuring servo: manual mode, ±3 V limits, conservative gains")
    servo.output_mode = "MAN"
    servo.output_upper_limit_V = output_limit_V
    servo.output_lower_limit_V = -output_limit_V
    servo.proportional_gain = prop_gain
    servo.integral_gain = intg_gain

    n_points = len(np.arange(v_start, v_stop, v_step))
    report(f"sweeping bias {v_start} V .. {v_stop} V ({n_points} points)")
    voltages, inputs = im_bias_scan(
        servo, v_start=v_start, v_stop=v_stop, v_step=v_step, settle_s=settle_s, sim=sim
    )

    idx_max, idx_min = int(np.argmax(inputs)), int(np.argmin(inputs))
    v_max, v_min = voltages[idx_max], voltages[idx_min]
    if v_max == v_min:
        raise RuntimeError(
            "IM sweep saw no modulation (flat response); check the RF drive and photodiode"
        )
    slope = (inputs[idx_max] - inputs[idx_min]) / (v_max - v_min)
    if slope < 0:
        servo.proportional_gain = -abs(prop_gain)
        report(f"negative slope ({slope:.3f}); proportional gain polarity flipped")

    target = (inputs[idx_max] + inputs[idx_min]) / 2
    best_idx = int(np.argmin(np.abs(inputs - target)))
    best_v = float(voltages[best_idx])
    report(f"midpoint bias {best_v:.3f} V (input {inputs[best_idx]:.4f} V)")

    servo.manual_output_V = best_v
    setpoint = servo.measure_input_V
    servo.output_offset_V = best_v
    servo.setpoint_V = setpoint

    report("engaging PID")
    servo.output_mode = "PID"

    return {
        "bias_V": best_v,
        "setpoint_V": float(setpoint),
        "slope_sign": -1 if slope < 0 else 1,
        "sweep_min_V": float(inputs[idx_min]),
        "sweep_max_V": float(inputs[idx_max]),
    }
