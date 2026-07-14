"""GUI preferences (e.g. the saved OSA default view).

Stored in the repo's ``config/gui.toml`` and committed (unlike the
git-ignored site config next to it): the saved defaults are part of how
the comb is operated, so they are versioned and shared. The path is
resolved relative to this package, not the working directory, so the
GUI finds it however it is launched.
"""

from __future__ import annotations

import logging
import tomllib
from pathlib import Path

import tomlkit

__all__ = ["GUI_CONFIG_PATH", "load_section", "save_section"]

GUI_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "gui.toml"

log = logging.getLogger(__name__)


def load_section(section: str, path: Path | None = None) -> dict:
    """One ``[section]`` table from the GUI config, ``{}`` if absent or
    unreadable (a broken prefs file must never stop the GUI)."""
    path = path or GUI_CONFIG_PATH
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        return {}
    except Exception as exc:  # noqa: BLE001 - malformed file -> defaults
        log.warning("could not read %s: %s", path, exc)
        return {}
    table = data.get(section, {})
    return dict(table) if isinstance(table, dict) else {}


def save_section(section: str, values: dict, path: Path | None = None) -> Path:
    """Write one ``[section]`` table, preserving the rest of the file."""
    path = path or GUI_CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        doc = tomlkit.parse(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        doc = tomlkit.document()
        doc.add(tomlkit.comment("keckogeco GUI preferences - written by the engineering GUI"))
    doc[section] = values
    path.write_text(tomlkit.dumps(doc), encoding="utf-8")
    log.info("saved [%s] to %s", section, path)
    return path
