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

if __package__ in (None, ""):  # run as a bare file (VSCode Run button)
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel

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


def _json_value(value):
    """NaN/Inf are not valid JSON (the response encoder rejects them, which
    would 500 the whole bulk read); report them as null. Real case: an OZ
    VOA reads NaN attenuation until it has homed."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
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
