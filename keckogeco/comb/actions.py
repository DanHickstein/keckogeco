"""Long-running comb actions: state transitions and setup sequences.

Ported from ``LFC_SET_STANDBY`` / ``LFC_SET_FULL_COMB`` / ``LFC_SET_OFF`` /
``LFC_MINICOMB_AUTO_SETUP`` (``KeckLFC.py:2252-2464``) as ordered,
abortable step sequences with progress reporting. The numbers (Pritel
600 mA preamp / 3.9 A power amp, RF amp 30 V 4.2 A, RF osc 15 V 3 A,
EDFA27 450 mW APC, the 1-10 mW input-power gates) are commissioning
values carried over verbatim. Exception: EDFA23 is parked at ACC 0 mA
(activated but dark) because the 23 dB EDFA is currently out of service
— restore its commissioned 80 mA and the seed-power gate when it
returns (see ktl/keyword-changes.md).

One action runs at a time on the :class:`ActionExecutor`'s thread; while
it runs, keyword writes are rejected by the server (reads stay allowed).
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

from .state import CombState

__all__ = ["ActionBusy", "ActionContext", "ActionExecutor", "ACTIONS"]

log = logging.getLogger(__name__)


class ActionBusy(Exception):
    """Another action is already running."""


class ActionAborted(Exception):
    """The running action was asked to stop."""


@dataclass
class ActionStatus:
    name: str
    message: str = "starting"
    step: int = 0
    total_steps: int = 0
    started: float = field(default_factory=time.time)
    finished: float | None = None
    error: str | None = None
    aborted: bool = False

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "message": self.message,
            "step": self.step,
            "total_steps": self.total_steps,
            "started": self.started,
            "finished": self.finished,
            "error": self.error,
            "aborted": self.aborted,
            "running": self.finished is None,
        }


class ActionContext:
    """Progress + abort plumbing handed to each action function."""

    def __init__(self, status: ActionStatus, abort_event: threading.Event, sim: bool):
        self._status = status
        self._abort = abort_event
        self.sim = sim

    def step(self, message: str, total: int | None = None) -> None:
        """Advance to the next step; raises if an abort was requested."""
        if self._abort.is_set():
            raise ActionAborted(f"aborted before: {message}")
        self._status.step += 1
        if total is not None:
            self._status.total_steps = total
        self._status.message = message
        log.info("[%s] step %d: %s", self._status.name, self._status.step, message)

    def tick(self, message: str) -> None:
        """Per-point progress: like :meth:`step` but logs at debug level
        (bias sweeps have hundreds of points; the log keeps stage
        boundaries only)."""
        if self._abort.is_set():
            raise ActionAborted(f"aborted at: {message}")
        self._status.step += 1
        self._status.message = message
        log.debug("[%s] %s", self._status.name, message)

    def sleep(self, seconds: float) -> None:
        """Abortable settle wait; skipped entirely in sim mode."""
        if self.sim:
            return
        if self._abort.wait(timeout=seconds):
            raise ActionAborted("aborted during settle wait")


class ActionExecutor:
    """Single-slot background runner for comb actions."""

    def __init__(self, controller):
        self.controller = controller
        self._thread: threading.Thread | None = None
        self._status: ActionStatus | None = None
        self._abort = threading.Event()
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def current(self) -> dict | None:
        with self._lock:
            return self._status.as_dict() if self._status else None

    def submit(self, name: str, **kwargs) -> dict:
        """Start the named action; ``kwargs`` are passed to the action
        function (e.g. the IM bias-scan range)."""
        func = ACTIONS.get(name)
        if func is None:
            raise KeyError(f"unknown action {name!r} (know {sorted(ACTIONS)})")
        with self._lock:
            if self.running:
                raise ActionBusy(f"action {self._status.name!r} is already running")
            self._abort.clear()
            status = ActionStatus(name=name)
            self._status = status
            context = ActionContext(status, self._abort, self.controller.sim)

            def run() -> None:
                try:
                    func(self.controller, context, **kwargs)
                    status.message = "done"
                except ActionAborted as exc:
                    status.aborted = True
                    status.message = str(exc)
                    log.warning("[%s] %s", name, exc)
                except Exception as exc:  # noqa: BLE001 - recorded, not raised into thread
                    status.error = str(exc)
                    status.message = f"FAILED: {exc}"
                    log.exception("[%s] failed", name)
                finally:
                    status.finished = time.time()

            self._thread = threading.Thread(target=run, name=f"action-{name}", daemon=True)
            self._thread.start()
            return status.as_dict()

    def abort(self) -> None:
        self._abort.set()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)


# --------------------------------------------------------------------------
# The sequences. Each takes (controller, ctx).
# --------------------------------------------------------------------------


def _pritel_down(controller, ctx: ActionContext) -> None:
    ptamp = controller.device("ptamp")
    ctx.step("Pritel power amp -> 0 A")
    ptamp.set_pwramp_mA(0)
    ctx.sleep(0.5)
    ctx.step("Pritel preamp -> 0 mA")
    ptamp.set_preamp_mA(0)
    ctx.sleep(0.5)
    ctx.step("Pritel pump OFF")
    ptamp.set_pump(False)
    ctx.sleep(0.5)


def _pritel_up(controller, ctx: ActionContext) -> None:
    ctx.step("resetting Pritel interlock latch")
    controller.device("arduino_relay").reset_latch()
    ctx.sleep(0.5)
    ptamp = controller.device("ptamp")
    ctx.step("Pritel preamp -> 600 mA")
    ptamp.set_preamp_mA(600)
    ctx.sleep(0.5)
    ctx.step("Pritel pump ON")
    ptamp.set_pump(True)
    ctx.sleep(0.5)
    ctx.step("Pritel power amp -> 3.9 A (ramped)")
    ptamp.set_pwramp_mA(3900)
    ctx.step("waiting 10 s for the amplifier to settle")
    ctx.sleep(10)


def close_all(controller, ctx: ActionContext) -> None:
    """Turn off every optical/RF output (LFC_CLOSE_ALL)."""
    for key in ("edfa27", "edfa23", "edfa13"):
        if key in controller.devices:
            ctx.step(f"{key} emission OFF")
            edfa = controller.device(key)
            edfa.deactivate()
            edfa.set_channel(False)
            ctx.sleep(0.3)
    for key in ("rf_osc_psu", "rf_amp_psu"):
        if key in controller.devices:
            ctx.step(f"{key} output OFF")
            controller.device(key).set_output(False, controller.psu_channel(key))
            ctx.sleep(0.3)


def minicomb_auto_setup(controller, ctx: ActionContext) -> None:
    """Bring up the minicomb: RF chain, then seeded EDFAs with power gates."""
    ctx.step("RF amplifier supply: 30 V / 4.2 A", total=12)
    rf_amp = controller.device("rf_amp_psu")
    channel = controller.psu_channel("rf_amp_psu")
    rf_amp.set_voltage_V(30, channel)
    ctx.sleep(0.5)
    rf_amp.set_current_A(4.2, channel)
    ctx.sleep(0.5)
    ctx.step("RF amplifier supply output ON")
    rf_amp.set_output(True, channel)
    ctx.sleep(0.3)

    ctx.step("RF oscillator supply: 15 V / 3 A")
    rf_osc = controller.device("rf_osc_psu")
    channel = controller.psu_channel("rf_osc_psu")
    rf_osc.set_voltage_V(15, channel)
    ctx.sleep(0.5)
    rf_osc.set_current_A(3, channel)
    ctx.sleep(0.5)
    ctx.step("RF oscillator supply output ON")
    rf_osc.set_output(True, channel)
    ctx.sleep(0.3)

    ctx.step("EDFA27: checking seed input power")
    edfa27 = controller.device("edfa27")
    input_power = edfa27.input_power_mW()
    if not 1 < input_power < 10:
        raise RuntimeError(
            f"EDFA27 input power {input_power:.2f} mW outside the safe 1-10 mW "
            "window; aborting minicomb setup"
        )
    ctx.step("EDFA27: APC 450 mW, channel + activation ON")
    edfa27.set_mode("APC")
    ctx.sleep(0.5)
    edfa27.set_setpoint(450)
    ctx.sleep(0.5)
    edfa27.set_channel(True)
    ctx.sleep(0.5)
    edfa27.activate()
    ctx.sleep(0.5)

    # EDFA23 is out of service: park it at ACC 0 mA but still activated,
    # so the legacy prime-product state code (which reads the activation
    # flag) can still reach FULL COMB. No seed gate at zero drive. When
    # the 23 dB EDFA returns to service, restore the 1-10 mW gate and
    # the commissioned 80 mA setpoint here.
    if "edfa23" in controller.devices:
        ctx.step("EDFA23: parked at ACC 0 mA (out of service), activation ON")
        edfa23 = controller.device("edfa23")
        edfa23.set_mode("ACC")
        ctx.sleep(0.5)
        edfa23.set_setpoint(0)
        ctx.sleep(0.5)
        edfa23.set_channel(True)
        edfa23.activate()
        ctx.sleep(0.5)
    else:
        ctx.step("EDFA23 skipped (not configured)")

    # The IM bias lock is engaged manually from the engineering GUI
    # (setpoint / bias / PI gains in the servo panel, then Lock) — the
    # old automated lock step was removed deliberately (Dan, 2026-07-15).
    ctx.step("minicomb up; engage the IM bias lock from the GUI when needed")


def set_standby(controller, ctx: ActionContext) -> None:
    state = controller.comb_state()
    ctx.step(f"current state: {state.value}", total=14)
    if state == CombState.STANDBY:
        ctx.step("already in STANDBY")
        return
    if state == CombState.FULL_COMB:
        _pritel_down(controller, ctx)
        return
    if state == CombState.OFF:
        minicomb_auto_setup(controller, ctx)
        return
    raise RuntimeError(f"cannot go to STANDBY from {state.value}; inspect the system first")


def set_full_comb(controller, ctx: ActionContext) -> None:
    state = controller.comb_state()
    ctx.step(f"current state: {state.value}", total=20)
    if state == CombState.FULL_COMB:
        ctx.step("already in FULL COMB")
        return
    if state == CombState.OFF:
        minicomb_auto_setup(controller, ctx)
        ctx.step("waiting 5 s for the minicomb to stabilize")
        ctx.sleep(5)
        state = CombState.STANDBY
    if state == CombState.STANDBY:
        _pritel_up(controller, ctx)
        return
    raise RuntimeError(f"cannot go to FULL COMB from {state.value}; inspect the system first")


def set_off(controller, ctx: ActionContext) -> None:
    state = controller.comb_state()
    ctx.step(f"current state: {state.value}", total=14)
    if state == CombState.OFF:
        ctx.step("already OFF")
        return
    if state in (CombState.FULL_COMB, CombState.STANDBY):
        _pritel_down(controller, ctx)
        close_all(controller, ctx)
        return
    raise RuntimeError(f"cannot go to OFF from {state.value}; inspect the system first")


def im_bias_scan_action(
    controller,
    ctx: ActionContext,
    v_start: float = -2.0,
    v_stop: float = 1.0,
    v_step: float = 0.02,
    settle_s: float = 0.2,
) -> None:
    """Sweep the IM bias and record the photodiode response — measurement
    only, no lock. Points stream into ``controller.im_scan_points`` (served
    as the ``im_scan`` array) and the pre-scan bias is restored at the end,
    abort included."""
    import numpy as np

    from .locking import im_bias_scan

    servo = getattr(controller, "_im_servo", None)
    if servo is None:
        raise RuntimeError("no SRS mainframe configured; cannot scan the IM bias")

    n_points = len(np.arange(v_start, v_stop, v_step))
    controller.im_scan_points.clear()
    ctx.step(
        f"IM bias scan {v_start:+.3f} .. {v_stop:+.3f} V, {n_points} points",
        total=n_points + 2,
    )
    # stale hardware limits (e.g. ±3 V from the old commissioned lock)
    # would silently clamp the sweep: pin the ±8 V operating limit first
    servo.output_upper_limit_V = 8.0
    servo.output_lower_limit_V = -8.0
    previous_V = servo.manual_output_V

    def point(_index: int, bias_V: float, input_V: float) -> None:
        ctx.tick(f"bias {bias_V:+.3f} V -> photodiode {input_V:.4f} V")
        controller.im_scan_points.append((bias_V, input_V))

    try:
        im_bias_scan(
            servo,
            v_start=v_start,
            v_stop=v_stop,
            v_step=v_step,
            settle_s=settle_s,
            sim=controller.sim,
            point=point,
        )
    finally:
        servo.manual_output_V = previous_V
        log.info(
            "IM bias scan done (%d points); bias restored to %+.3f V",
            len(controller.im_scan_points),
            previous_V,
        )
    ctx.step(f"scan complete; bias restored to {previous_V:+.3f} V")


ACTIONS = {
    "set_standby": set_standby,
    "set_full_comb": set_full_comb,
    "set_off": set_off,
    "minicomb_auto_setup": minicomb_auto_setup,
    "close_all": close_all,
    "im_bias_scan": im_bias_scan_action,
}
