"""HTTP/REST server exposing the LFCController (FastAPI)."""

from .app import create_app

__all__ = ["create_app"]
