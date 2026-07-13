"""HTTP/REST server exposing the LFCController (FastAPI)."""

__all__ = ["create_app"]


def __getattr__(name):
    # Lazy so `python -m keckogeco.server.app` doesn't import app twice
    # (runpy warns when the target module is imported during package init).
    if name == "create_app":
        from .app import create_app

        return create_app
    raise AttributeError(name)
