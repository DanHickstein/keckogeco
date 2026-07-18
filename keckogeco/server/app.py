"""FastAPI application: the single owner of the instruments.

Runs on the Windows control laptop. Everything else — the PyQt
engineering GUI, the web status page, and the Keck-side KTL dispatcher —
talks to this API; only this process touches COM ports.

Threading model: handlers are sync functions in FastAPI's threadpool;
per-instrument locks (in the driver base class) serialize I/O per device
while different devices proceed in parallel. A background poller refreshes
the keyword cache so bulk reads and dispatcher polling never fan out into
dozens of serial transactions. Long sequences (comb transitions,
autolocks) get a single-slot action executor in Phase 2.
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import math
import threading
from importlib.metadata import version as pkg_version
from pathlib import Path
from typing import Literal

if __package__ in (None, ""):  # run as a bare file (VSCode Run button)
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from keckogeco.comb.actions import ACTIONS, ActionBusy
from keckogeco.comb.controller import LFCController
from keckogeco.comb.keywords import KeywordError
from keckogeco.config import Config, ConfigError, example_config_path, load_config
from keckogeco.drivers.errors import InstrumentError
from keckogeco.logsetup import setup_logging

__all__ = ["create_app", "main"]

log = logging.getLogger(__name__)

API_PREFIX = "/api/v1"


class WriteRequest(BaseModel):
    value: str | float | int | bool | list[float]


class OsaSettingsRequest(BaseModel):
    """Partial update: only the fields present are written to the OSA."""

    start_nm: float | None = None
    stop_nm: float | None = None
    resolution_nm: float | None = None
    sensitivity_dBm: float | None = None


class OsaSweepRequest(BaseModel):
    mode: Literal["single", "continuous", "stop"]


class ImScanRequest(BaseModel):
    """IM bias scan range. Bounds are the ±8 V operating limit chosen to
    stay under the SIM960's ±10 V output spec (Dan, 2026-07-15); the
    default sweep covers ±5 V. Settle times under ~1 s make MMON return
    the same reading for several consecutive points."""

    v_start: float = Field(-5.0, ge=-8.0, le=8.0)
    v_stop: float = Field(5.0, ge=-8.0, le=8.0)
    v_step: float = Field(0.2, ge=0.002, le=0.5)
    settle_s: float = Field(1.0, ge=0.0, le=5.0)


class SliderRequest(BaseModel):
    """Target slot of the flattener's ND-filter slider (Thorlabs ELL12)."""

    position: int = Field(ge=1, le=6)


class ImSettingsRequest(BaseModel):
    """Partial update of the IM servo: only the fields present are written.
    The setpoint is the photodiode voltage the PID holds when locked; the
    PI gains are the SIM960's (P sign sets the feedback polarity; bounds
    mirror the module's documented ranges)."""

    setpoint_V: float | None = Field(None, ge=-10.0, le=10.0)
    prop_gain: float | None = Field(None, ge=-1000.0, le=1000.0)
    intg_gain: float | None = Field(None, ge=0.01, le=5e5)


def _json_value(value):
    """NaN/Inf are not valid JSON: convert them (also inside arrays) to
    None explicitly, so clients see null regardless of how the installed
    FastAPI/Starlette version encodes non-finite floats (probed 2026-07-14:
    the current stack nulls them, a plain ``json.dumps`` rejects them).
    Real cases: an OZ VOA reads NaN attenuation until it has homed, and
    LFC_TEMP_TEST1 carries NaN for the rack DAQ's open-ch7 thermocouple."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, list):
        return [_json_value(v) for v in value]
    return value


class Poller(threading.Thread):
    """Background refresh of every bound keyword into the registry cache."""

    def __init__(self, controller: LFCController, period_s: float = 5.0):
        super().__init__(name="keyword-poller", daemon=True)
        self.controller = controller
        self.period_s = period_s
        self._stop_event = threading.Event()

    def run(self) -> None:
        registry = self.controller.registry
        while not self._stop_event.is_set():
            for name in sorted(registry._getters):
                if self._stop_event.is_set():
                    break
                try:
                    registry.read(name)
                except Exception as exc:  # noqa: BLE001 - keep polling the rest
                    log.debug("poll %s failed: %s", name, exc)
            self._stop_event.wait(self.period_s)

    def stop(self) -> None:
        self._stop_event.set()


def create_app(config: Config, sim: bool = False, poll_s: float = 5.0) -> FastAPI:
    controller = LFCController(config, sim=sim)
    poller = Poller(controller, period_s=poll_s)

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        controller.start()
        if poll_s > 0:
            poller.start()
        yield
        poller.stop()
        controller.stop()

    app = FastAPI(title="keckogeco", version=_version(), lifespan=lifespan)
    app.state.controller = controller

    def require_token(request: Request) -> None:
        token = config.server.api_token
        if not token:
            return
        supplied = request.headers.get("Authorization", "")
        if supplied != f"Bearer {token}":
            raise HTTPException(status_code=401, detail="missing or invalid bearer token")

    auth = Depends(require_token)

    def keyword_spec(name: str):
        spec = controller.registry.schema.get(name)
        if spec is None:
            raise HTTPException(status_code=404, detail=f"unknown keyword {name!r}")
        return spec

    @app.get(f"{API_PREFIX}/health")
    def health() -> dict:
        return {
            "status": "ok",
            "version": _version(),
            "sim": controller.sim,
            "devices_online": sorted(controller.devices),
            "devices_offline": controller.offline,
            "keywords_bound": len(controller.registry.bound),
        }

    @app.get(f"{API_PREFIX}/keywords", dependencies=[auth])
    def keywords() -> dict:
        """Bulk cached snapshot; no hardware I/O."""
        out = {}
        for name, kv in controller.registry.snapshot().items():
            spec = controller.registry.schema[name]
            out[name] = {
                "value": _json_value(kv.value),
                "timestamp": kv.timestamp,
                "type": spec.type,
                "units": spec.units,
            }
        return out

    @app.get(f"{API_PREFIX}/keywords/{{name}}", dependencies=[auth])
    def read_keyword(name: str, fresh: bool = False) -> dict:
        spec = keyword_spec(name)
        if not fresh:
            cached = controller.registry.snapshot().get(name)
            if cached is not None:
                return {
                    "name": name,
                    "value": _json_value(cached.value),
                    "timestamp": cached.timestamp,
                }
        try:
            kv = controller.read(name)
        except KeywordError as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from exc
        except InstrumentError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {
            "name": name,
            "value": _json_value(kv.value),
            "timestamp": kv.timestamp,
            "units": spec.units,
        }

    @app.put(f"{API_PREFIX}/keywords/{{name}}", dependencies=[auth])
    def write_keyword(name: str, body: WriteRequest) -> dict:
        keyword_spec(name)
        if controller.executor.running:
            action = controller.executor.current() or {}
            raise HTTPException(
                status_code=409,
                detail=f"action {action.get('name')!r} is running; writes are locked "
                "until it finishes (reads are fine)",
            )
        try:
            value = controller.write(name, body.value)
        except KeywordError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ActionBusy as exc:  # e.g. modify LFC_SET_STANDBY during an action
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except InstrumentError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"name": name, "value": value}

    @app.post(f"{API_PREFIX}/actions/{{name}}", dependencies=[auth])
    def start_action(name: str) -> dict:
        if name not in ACTIONS:
            raise HTTPException(
                status_code=404, detail=f"unknown action {name!r}; know {sorted(ACTIONS)}"
            )
        try:
            return controller.executor.submit(name)
        except ActionBusy as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get(f"{API_PREFIX}/actions/current", dependencies=[auth])
    def current_action() -> dict:
        return controller.executor.current() or {"running": False}

    @app.delete(f"{API_PREFIX}/actions/current", dependencies=[auth])
    def abort_action() -> dict:
        controller.executor.abort()
        return controller.executor.current() or {"running": False}

    @app.get(f"{API_PREFIX}/schema", dependencies=[auth])
    def schema() -> dict:
        """The full keyword schema (types, units, limits, help) so GUIs can
        build controls without hardcoding."""
        return {
            name: {
                "type": s.type,
                "writable": s.writable,
                "units": s.units,
                "min": s.min,
                "max": s.max,
                "enum": s.enum,
                "help": s.help,
                "bound": name in controller.registry.bound,
            }
            for name, s in controller.registry.schema.items()
        }

    @app.get(f"{API_PREFIX}/state", dependencies=[auth])
    def state() -> dict:
        return controller.state_summary()

    @app.get(f"{API_PREFIX}/devices", dependencies=[auth])
    def devices() -> dict:
        """Configured devices with their addresses (GUIs show e.g. the COM
        port in panel titles); no hardware I/O."""
        return {
            key: {
                "name": dev.name,
                "driver": dev.driver,
                "address": dev.address,
                "enabled": dev.enabled,
                "online": key in controller.devices,
                "offline_reason": controller.offline.get(key),
            }
            for key, dev in controller.config.devices.items()
        }

    @app.get(f"{API_PREFIX}/interlock", dependencies=[auth])
    def interlock() -> dict:
        """Arduino interlock relay status with the ADC counts scaled to
        volts (10-bit over 0-5 V) — the GUI shows the trip window and
        colors the live voltage against it."""
        try:
            relay = controller.device("arduino_relay").relay_status()
        except InstrumentError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        to_volts = 5.0 / 1023.0
        return {
            "voltage_V": relay.voltage_now * to_volts,
            "low_threshold_V": relay.low_threshold * to_volts,
            "high_threshold_V": relay.high_threshold * to_volts,
            "ok_to_amplify": relay.ok_to_amplify,
            "resettable": relay.resettable,
        }

    @app.get(f"{API_PREFIX}/arrays", dependencies=[auth])
    def list_arrays() -> dict:
        return {"arrays": sorted(getattr(controller, "arrays", {}))}

    @app.get(f"{API_PREFIX}/arrays/{{name}}", dependencies=[auth])
    def read_array(name: str) -> dict:
        source = getattr(controller, "arrays", {}).get(name)
        if source is None:
            raise HTTPException(status_code=404, detail=f"unknown array {name!r}")
        try:
            return {"name": name, **source()}
        except InstrumentError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    # --- OSA control (engineering-GUI surface, deliberately NOT KTL
    # keywords: resolution/sensitivity/sweep are instrument-level knobs
    # that Keck operations never touch, so they stay out of the contract).

    def osa_device():
        try:
            return controller.device("osa")
        except InstrumentError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    def refuse_during_action() -> None:
        if controller.executor.running:
            action = controller.executor.current() or {}
            raise HTTPException(
                status_code=409,
                detail=f"action {action.get('name')!r} is running; writes are locked "
                "until it finishes (reads are fine)",
            )

    @app.get(f"{API_PREFIX}/osa", dependencies=[auth])
    def osa_settings() -> dict:
        osa = osa_device()
        try:
            return {**osa.status(), "resolutions_nm": list(osa.RESOLUTIONS_NM)}
        except InstrumentError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.put(f"{API_PREFIX}/osa", dependencies=[auth])
    def osa_apply(body: OsaSettingsRequest) -> dict:
        refuse_during_action()
        osa = osa_device()
        try:
            osa.set_range(body.start_nm, body.stop_nm)
            if body.resolution_nm is not None:
                osa.resolution_nm = body.resolution_nm
            if body.sensitivity_dBm is not None:
                osa.sensitivity_dBm = body.sensitivity_dBm
            # read back so the GUI shows what the instrument accepted
            return {**osa.status(), "resolutions_nm": list(osa.RESOLUTIONS_NM)}
        except InstrumentError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    # --- IM bias scan (engineering-GUI surface, like the OSA endpoints:
    # sweep-and-plot is a commissioning tool, not part of the KTL contract).
    # The scan runs on the single-slot action executor, so /actions/current
    # reports progress and DELETE /actions/current aborts it.

    def im_servo():
        servo = getattr(controller, "_im_servo", None)
        if servo is None:
            raise HTTPException(status_code=503, detail="SRS SIM900 not configured or offline")
        return servo

    def im_live(servo) -> dict:
        """Servo readouts with the same keys as the im_scan array payload,
        so the GUI panel can populate from either. Gains are included here
        (on-demand GET/PUT read-backs) but deliberately NOT in the array
        payload — they change rarely and the array is polled at ~1 Hz."""
        return {
            "mode": servo.output_mode,
            "setpoint_V": servo.setpoint_V,
            "bias_V": servo.output_V,
            "input_V": servo.measure_input_V,
            "prop_gain": servo.proportional_gain,
            "intg_gain": servo.integral_gain,
        }

    @app.get(f"{API_PREFIX}/im", dependencies=[auth])
    def im_status() -> dict:
        try:
            return im_live(im_servo())
        except InstrumentError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.put(f"{API_PREFIX}/im", dependencies=[auth])
    def im_apply(body: ImSettingsRequest) -> dict:
        refuse_during_action()
        servo = im_servo()
        try:
            if body.setpoint_V is not None:
                servo.setpoint_V = body.setpoint_V
            if body.prop_gain is not None:
                servo.proportional_gain = body.prop_gain
            if body.intg_gain is not None:
                servo.integral_gain = body.intg_gain
            return im_live(servo)
        except InstrumentError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post(f"{API_PREFIX}/im/scan", dependencies=[auth])
    def im_scan_start(body: ImScanRequest) -> dict:
        servo = im_servo()
        if body.v_stop - body.v_start < body.v_step:
            raise HTTPException(
                status_code=400,
                detail=f"empty scan: v_start {body.v_start} .. v_stop {body.v_stop} "
                f"in {body.v_step} V steps",
            )
        try:
            if servo.output_mode == "PID":
                raise HTTPException(
                    status_code=409,
                    detail="IM lock is engaged (PID mode); unlock before scanning",
                )
        except InstrumentError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        # never sweep under the Pritel (issue #43): the sweep crosses
        # fringe nulls that starve the amplifier's seed, and no bound
        # monitor would show it (the ASD is the last line of defense)
        try:
            if controller.device("ptamp").pump_on:
                raise HTTPException(
                    status_code=409,
                    detail="the Pritel amplifier is ON; turn it off before scanning "
                    "(the sweep crosses bias points that starve the amplifier's seed)",
                )
        except InstrumentError as exc:
            # not configured / offline: can't verify, don't block the scan
            log.warning("IM scan: cannot verify the Pritel is off (%s)", exc)
        try:
            return controller.executor.submit(
                "im_bias_scan",
                v_start=body.v_start,
                v_stop=body.v_stop,
                v_step=body.v_step,
                settle_s=body.settle_s,
            )
        except ActionBusy as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    def srs_device():
        try:
            return controller.device("srs")
        except InstrumentError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get(f"{API_PREFIX}/im/modules", dependencies=[auth])
    def im_modules() -> dict:
        """SIM900 slot inventory (*IDN? per slot; null = empty slot).
        Diagnostic: identifies which slots hold the SIM960 servos when
        the commissioned slot map is in doubt."""
        try:
            inventory = srs_device().module_inventory()
        except InstrumentError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"modules": {str(slot): idn for slot, idn in inventory.items()}}

    @app.get(f"{API_PREFIX}/im/servo/{{slot}}", dependencies=[auth])
    def im_servo_status(slot: int) -> dict:
        """Read-only status of the SIM960 in the given slot (mode, gains,
        setpoint, output, measure input). Diagnostic — reads any slot, not
        just the configured im_slot; writes still go through im_slot only."""
        if not 1 <= slot <= 8:
            raise HTTPException(status_code=400, detail="slot must be 1..8")
        servo = srs_device().sim960(slot, f"SIM960@{slot}")
        try:
            return {"slot": slot, **servo.status()}
        except InstrumentError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    # --- spectral-flattener ND slider (engineering-GUI surface, like the
    # OSA endpoints: the slider was never a KTL keyword — it historically
    # lived on the Menlo laptop's ELLO software, so it stays out of the
    # contract). 503 while the slider is not connected to this laptop.

    def slider_device():
        try:
            return controller.device("nd_slider")
        except InstrumentError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get(f"{API_PREFIX}/flattener/slider", dependencies=[auth])
    def slider_status() -> dict:
        try:
            return slider_device().status()
        except InstrumentError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.put(f"{API_PREFIX}/flattener/slider", dependencies=[auth])
    def slider_move(body: SliderRequest) -> dict:
        refuse_during_action()
        slider = slider_device()
        try:
            slider.set_position(body.position)
            return slider.status()
        except InstrumentError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post(f"{API_PREFIX}/flattener/slider/home", dependencies=[auth])
    def slider_home() -> dict:
        """Re-reference the slider (needed once after a power-up)."""
        refuse_during_action()
        slider = slider_device()
        try:
            slider.home()
            return slider.status()
        except InstrumentError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post(f"{API_PREFIX}/osa/sweep", dependencies=[auth])
    def osa_sweep(body: OsaSweepRequest) -> dict:
        refuse_during_action()
        osa = osa_device()
        try:
            if body.mode == "single":
                osa.trigger_single()
            else:
                osa.sweep_continuous = body.mode == "continuous"
            return {"mode": body.mode, "sweep_continuous": osa.sweep_continuous}
        except InstrumentError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    # Static web status page at / (added last so API routes win). The page
    # itself is public like /health; its API calls still honor the token.
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=Path(__file__).parent / "web", html=True), name="web")

    return app


def _version() -> str:
    try:
        return pkg_version("keckogeco")
    except Exception:  # noqa: BLE001 - not installed (e.g. vendored)
        from keckogeco import __version__

        return __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="keckogeco control server")
    parser.add_argument("--config", default=None, help="config file path")
    parser.add_argument("--sim", action="store_true", help="simulated instruments")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--poll", type=float, default=5.0, help="cache poll period, s (0 = off)")
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except ConfigError:
        # --sim needs no real addresses: fall back to the bundled example
        # so the server runs on any fresh checkout.
        example = example_config_path() if args.sim and args.config is None else None
        if example is None:
            raise
        config = load_config(example)
        log.info("sim mode: no site config found, using %s", example)
    setup_logging(config.logging)

    import uvicorn

    app = create_app(config, sim=args.sim, poll_s=args.poll)
    # access_log=False: the GUIs poll every few seconds, so per-request
    # lines drown out the interesting events. Errors still log.
    uvicorn.run(
        app,
        host=args.host or config.server.host,
        port=args.port or config.server.port,
        log_level="info",
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
