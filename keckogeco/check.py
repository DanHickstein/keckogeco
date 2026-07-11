"""``keckogeco-check``: validate the config and try talking to each device.

Replaces the 17 ad-hoc ``if __name__ == "__main__"`` blocks in the old
drivers with one place to sanity-check an installation:

* loads and validates the config file,
* builds every enabled device (``--sim`` for no hardware),
* connects to each in turn and prints its ``status()`` summary.
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import sys

from .config import Config, ConfigError, DeviceConfig, load_config
from .drivers.base import Instrument
from .logsetup import setup_logging

__all__ = ["build_device", "main"]


def driver_class(driver_name: str) -> type[Instrument]:
    """Resolve a driver module name (e.g. ``"amonics_edfa"``) to its
    Instrument subclass."""
    module = importlib.import_module(f".drivers.{driver_name}", package="keckogeco")
    classes = [
        obj
        for _, obj in inspect.getmembers(module, inspect.isclass)
        if issubclass(obj, Instrument)
        and obj is not Instrument
        and obj.__module__ == module.__name__
    ]
    if not classes:
        raise ImportError(f"No Instrument subclass found in keckogeco.drivers.{driver_name}")
    if len(classes) > 1:
        # modules like srs_sim900 hold helpers; pick the one named in __all__
        exported = getattr(module, "__all__", [])
        classes = [c for c in classes if c.__name__ in exported] or classes
    return classes[0]


def build_device(cfg: DeviceConfig, sim: bool = False) -> Instrument:
    """Instantiate the configured driver for one device block."""
    return driver_class(cfg.driver).from_config(cfg, sim=sim)


def check_all(config: Config, sim: bool = False, connect: bool = True) -> dict[str, str]:
    """Try each enabled device; return {key: 'ok' | error message}."""
    results: dict[str, str] = {}
    for key, dev_cfg in config.enabled_devices().items():
        try:
            device = build_device(dev_cfg, sim=sim)
        except Exception as exc:  # noqa: BLE001 - report, don't crash the sweep
            results[key] = f"build failed: {exc}"
            continue
        if not connect:
            results[key] = "ok (not connected)"
            continue
        try:
            with device:
                status = device.status() if hasattr(device, "status") else {}
                results[key] = f"ok {status}" if status else "ok"
        except Exception as exc:  # noqa: BLE001
            results[key] = f"connect failed: {exc}"
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate config and try each instrument.")
    parser.add_argument("--config", default=None, help="config file path")
    parser.add_argument("--sim", action="store_true", help="simulated transports (no hardware)")
    parser.add_argument(
        "--no-connect", action="store_true", help="only validate config and build drivers"
    )
    parser.add_argument("--device", help="check a single device key")
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)  # noqa: T201
        return 2
    setup_logging(config.logging)

    print(f"Config: {config.source}")  # noqa: T201
    devices = config.enabled_devices()
    if args.device:
        if args.device not in devices:
            print(f"No enabled device {args.device!r} in config", file=sys.stderr)  # noqa: T201
            return 2
        devices = {args.device: devices[args.device]}
        config = Config(
            server=config.server,
            logging=config.logging,
            alerts=config.alerts,
            devices=devices,
            source=config.source,
        )
    print(f"{len(devices)} enabled device(s){' [SIM]' if args.sim else ''}:")  # noqa: T201

    results = check_all(config, sim=args.sim, connect=not args.no_connect)
    failures = 0
    for key, result in results.items():
        ok = result.startswith("ok")
        failures += 0 if ok else 1
        marker = "PASS" if ok else "FAIL"
        print(f"  [{marker}] {key:20s} {result}")  # noqa: T201
    print(f"\n{len(results) - failures}/{len(results)} devices OK")  # noqa: T201
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
