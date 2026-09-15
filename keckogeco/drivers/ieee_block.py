"""IEEE-488.2 definite-length block decoding, shared by the OSA drivers.

Both the Agilent 86142B and the Yokogawa AQ63xx transfer traces as
``FORM REAL,32`` blocks (``#<digits><nbytes><float32 payload>``), but with
opposite byte orders: the Agilent is big-endian, the AQ6376 sends
little-endian (rack-verified 2026-07-21). Each driver passes its native
order; the sanity-check fallback covers a unit that disagrees.
"""

from __future__ import annotations

import numpy as np

from .errors import ResponseError

__all__ = ["parse_float32_block"]


def parse_float32_block(raw: bytes, name: str, byteorder: str = ">") -> np.ndarray:
    """Decode a definite-length block of float32 dBm values.

    Parameters
    ----------
    raw : bytes
        The full reply, starting at the ``#`` header.
    name : str
        Instrument name for error messages.
    byteorder : str
        The instrument's native float order, ``">"`` or ``"<"``.
    """
    if not raw.startswith(b"#") or len(raw) < 2:
        raise ResponseError(f"{name}: unparseable trace data {raw[:80]!r}")
    try:
        digits = int(raw[1:2])
        nbytes = int(raw[2 : 2 + digits])
    except ValueError as exc:
        raise ResponseError(f"{name}: unparseable trace data {raw[:80]!r}") from exc
    payload = raw[2 + digits : 2 + digits + nbytes]
    if len(payload) < nbytes or nbytes % 4:
        raise ResponseError(f"{name}: trace block truncated ({len(payload)}/{nbytes} bytes)")
    swapped_order = "<" if byteorder == ">" else ">"
    # errstate: wrong-order bytes can form signaling NaNs whose cast warns
    with np.errstate(invalid="ignore"):
        power = np.frombuffer(payload, dtype=f"{byteorder}f4").astype(np.float64)
        # dBm values live within ±210; anything wilder means the instrument
        # uses the other byte order and the floats must be swapped
        if power.size and (not np.isfinite(power).all() or np.abs(power).max() > 1e3):
            swapped = np.frombuffer(payload, dtype=f"{swapped_order}f4").astype(np.float64)
            if np.isfinite(swapped).all() and np.abs(swapped).max() <= 1e3:
                power = swapped
    return power
