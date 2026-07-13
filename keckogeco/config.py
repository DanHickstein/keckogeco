"""Configuration loading for keckogeco.

All site-specific settings — instrument addresses, server host/port,
log locations — live in a single TOML file that is *not* committed to git
(see ``config/instruments.example.toml`` for the template). This module
finds that file, parses it, and hands the rest of the package typed
dataclasses instead of raw dicts.

Search order for the config file:

1. explicit path (``--config`` flag / ``load_config(path=...)``)
2. ``KECKOGECO_CONFIG`` environment variable
3. ``~/.keckogeco/keckogeco.toml``
4. ``./config/keckogeco.toml`` (relative to the current working directory)

Run as a script (``python -m keckogeco.config``) it prints the configured
instrument list — a quick, read-only view of the TOML with the discovery
bookkeeping keys hidden. It never touches hardware; use
``python -m keckogeco.check`` to actually talk to the instruments.
"""

from __future__ import annotations

import argparse
import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

if __package__ in (None, ""):  # run as a bare file (VSCode Run button)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

__all__ = [
    "AlertsConfig",
    "Config",
    "ConfigError",
    "DeviceConfig",
    "LoggingConfig",
    "ServerConfig",
    "device_summary_lines",
    "find_config_file",
    "load_config",
    "main",
]

CONFIG_FILENAME = "keckogeco.toml"
ENV_VAR = "KECKOGECO_CONFIG"


class ConfigError(Exception):
    """Raised when the configuration file is missing or invalid."""


@dataclass(frozen=True)
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8000
    api_token: str = ""


@dataclass(frozen=True)
class LoggingConfig:
    dir: str = "logs"
    level: str = "INFO"
    #: seconds between telemetry CSV rows; 0 disables the telemetry logger
    telemetry_s: float = 30.0


@dataclass(frozen=True)
class AlertsConfig:
    enabled: bool = False


@dataclass(frozen=True)
class DeviceConfig:
    """One ``[devices.<key>]`` block.

    Parameters
    ----------
    key : str
        The block name, used as the device's identifier throughout the
        package (e.g. ``"edfa27"``).
    driver : str
        Module name in :mod:`keckogeco.drivers`.
    address : str
        Transport address — VISA resource string, COM port, host:port,
        or vendor serial number, depending on the driver.
    enabled : bool
        Disabled devices are skipped entirely at startup.
    name : str
        Human-readable label for logs and GUIs.
    options : dict
        Any extra keys from the block (``baud_rate``, ``model``,
        ``usb_serial``, ...), passed through to the driver.
    """

    key: str
    driver: str
    address: str
    enabled: bool = True
    name: str = ""
    options: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Config:
    server: ServerConfig
    logging: LoggingConfig
    alerts: AlertsConfig
    devices: dict[str, DeviceConfig]
    source: Path | None = None

    def enabled_devices(self) -> dict[str, DeviceConfig]:
        return {k: d for k, d in self.devices.items() if d.enabled}


def example_config_path() -> Path | None:
    """The bundled example config, if it can be found.

    Sim mode falls back to this so ``--sim`` works on a fresh checkout
    with no site config (the example ships at the repo root, so this
    resolves in git checkouts and editable installs).
    """
    candidates = [
        Path(__file__).resolve().parent.parent / "config" / "instruments.example.toml",
        Path("config") / "instruments.example.toml",
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


def find_config_file(explicit: str | Path | None = None) -> Path:
    """Locate the config file, following the documented search order."""
    candidates: list[Path] = []
    if explicit is not None:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise ConfigError(f"Config file not found: {path}")
        return path
    if ENV_VAR in os.environ:
        candidates.append(Path(os.environ[ENV_VAR]).expanduser())
    candidates.append(Path.home() / ".keckogeco" / CONFIG_FILENAME)
    candidates.append(Path("config") / CONFIG_FILENAME)
    for path in candidates:
        if path.is_file():
            return path
    searched = "\n  ".join(str(p) for p in candidates)
    raise ConfigError(
        f"No {CONFIG_FILENAME} found. Searched:\n  {searched}\n"
        "Copy config/instruments.example.toml to one of these locations "
        "and fill in your instrument addresses (or run python -m keckogeco.discovery)."
    )


def _parse_device(key: str, block: dict) -> DeviceConfig:
    if not isinstance(block, dict):
        raise ConfigError(f"[devices.{key}] must be a table, got {type(block).__name__}")
    missing = [f for f in ("driver", "address") if f not in block]
    if missing:
        raise ConfigError(f"[devices.{key}] is missing required key(s): {', '.join(missing)}")
    known = {"driver", "address", "enabled", "name"}
    return DeviceConfig(
        key=key,
        driver=str(block["driver"]),
        address=str(block["address"]),
        enabled=bool(block.get("enabled", True)),
        name=str(block.get("name", key)),
        options={k: v for k, v in block.items() if k not in known},
    )


def parse_config(data: dict, source: Path | None = None) -> Config:
    """Build a :class:`Config` from already-parsed TOML data."""
    server_raw = data.get("server", {})
    logging_raw = data.get("logging", {})
    alerts_raw = data.get("alerts", {})
    devices_raw = data.get("devices", {})
    if not isinstance(devices_raw, dict):
        raise ConfigError("[devices] must be a table of device blocks")
    try:
        server = ServerConfig(
            host=str(server_raw.get("host", ServerConfig.host)),
            port=int(server_raw.get("port", ServerConfig.port)),
            api_token=str(server_raw.get("api_token", "")),
        )
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"Invalid [server] section: {exc}") from exc
    logging_cfg = LoggingConfig(
        dir=str(logging_raw.get("dir", LoggingConfig.dir)),
        level=str(logging_raw.get("level", LoggingConfig.level)).upper(),
        telemetry_s=float(logging_raw.get("telemetry_s", LoggingConfig.telemetry_s)),
    )
    alerts = AlertsConfig(enabled=bool(alerts_raw.get("enabled", False)))
    devices = {key: _parse_device(key, block) for key, block in devices_raw.items()}
    return Config(server=server, logging=logging_cfg, alerts=alerts, devices=devices, source=source)


def load_config(path: str | Path | None = None) -> Config:
    """Find, read, and parse the configuration file."""
    config_path = find_config_file(path)
    try:
        with open(config_path, "rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Could not parse {config_path}: {exc}") from exc
    return parse_config(data, source=config_path)


def device_summary_lines(config: Config) -> list[str]:
    """One aligned text line per configured device, disabled ones included.

    Discovery bookkeeping options (``usb_serial``, ``probe``, ``found_on``,
    ...) are hidden; curated options like ``mode`` and ``channel`` are shown.
    """
    # Imported here because drivers.base imports this module (DeviceConfig).
    from keckogeco.drivers.base import DISCOVERY_KEYS

    rows = []
    for dev in config.devices.values():
        options = " ".join(
            f"{k}={v}" for k, v in sorted(dev.options.items()) if k not in DISCOVERY_KEYS
        )
        note = dev.name if dev.enabled else f"{dev.name} [disabled]"
        rows.append((dev.key, dev.driver, dev.address, options, note))
    widths = [max((len(row[i]) for row in rows), default=0) for i in range(4)]
    return [
        "  ".join([*(cell.ljust(w) for cell, w in zip(row[:4], widths, strict=True)), row[4]]).rstrip()
        for row in rows
    ]


def main(argv: list[str] | None = None) -> int:
    """``python -m keckogeco.config``: print the configured instrument list."""
    parser = argparse.ArgumentParser(
        description="Show the instrument list from the config file (read-only, no hardware I/O)."
    )
    parser.add_argument("--config", default=None, help="config file path")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)  # noqa: T201
        return 2
    print(f"Config: {config.source}")  # noqa: T201
    print(f"{len(config.devices)} device(s), {len(config.enabled_devices())} enabled\n")  # noqa: T201
    for line in device_summary_lines(config):
        print(f"  {line}")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
