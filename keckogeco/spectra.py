"""CSV save/load for OSA spectra.

Files carry their acquisition metadata (wavelength range, resolution,
sensitivity, ...) as ``# key: value`` header lines above a plain
``wavelength_nm,power_dBm`` table, so a spectrum stays interpretable on
its own and reloads with its context intact.
"""

from __future__ import annotations

import time
from pathlib import Path

__all__ = ["load_spectrum_csv", "save_spectrum_csv"]


def save_spectrum_csv(path: str | Path, x: list, y: list, metadata: dict) -> Path:
    """Write one spectrum; ``saved`` timestamp is added automatically."""
    path = Path(path)
    if len(x) != len(y):
        raise ValueError(f"x has {len(x)} points, y has {len(y)}")
    lines = ["# keckogeco OSA spectrum", f"# saved: {time.strftime('%Y-%m-%dT%H:%M:%S')}"]
    lines += [f"# {key}: {value}" for key, value in metadata.items()]
    lines.append("wavelength_nm,power_dBm")
    lines += [f"{float(xi):.6g},{float(yi):.6g}" for xi, yi in zip(x, y, strict=True)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def load_spectrum_csv(path: str | Path) -> tuple[list[float], list[float], dict]:
    """(x, y, metadata) from a spectrum CSV; tolerates missing headers."""
    x: list[float] = []
    y: list[float] = []
    metadata: dict[str, str] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            key, sep, value = line.lstrip("# ").partition(":")
            if sep:
                metadata[key.strip()] = value.strip()
            continue
        first, sep, second = line.partition(",")
        try:
            xi, yi = float(first), float(second)
        except ValueError:
            continue  # column-header row (or stray text)
        x.append(xi)
        y.append(yi)
    if not x:
        raise ValueError(f"{path}: no numeric wavelength,power rows found")
    return x, y, metadata
