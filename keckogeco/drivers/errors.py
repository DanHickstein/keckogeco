"""Exception hierarchy for instrument drivers."""

from __future__ import annotations

__all__ = ["ConnectionLost", "InstrumentError", "NotConnected", "ResponseError"]


class InstrumentError(Exception):
    """Base class for all instrument-related failures."""


class NotConnected(InstrumentError):
    """An operation was attempted on an instrument that is not connected."""


class ConnectionLost(InstrumentError):
    """I/O failed and a single automatic reconnect attempt also failed."""


class ResponseError(InstrumentError):
    """The instrument replied, but the reply could not be parsed.

    The old drivers returned sentinel strings (or crashed later) on garbage
    replies; raising keeps the failure at the point where it happened.
    """
