"""``keckogeco-gui``: launch the engineering GUI.

Talks to a running ``keckogeco-server`` (start one with ``--sim`` for an
offline layout check).
"""

from __future__ import annotations

import argparse
import sys

__all__ = ["main"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="keckogeco engineering GUI")
    parser.add_argument("--url", default="http://localhost:8000", help="keckogeco-server base URL")
    parser.add_argument("--token", default="", help="API bearer token, if the server uses one")
    args = parser.parse_args(argv)

    from PyQt6.QtWidgets import QApplication, QMessageBox

    from .client import KeckogecoClient
    from .mainwindow import MainWindow

    app = QApplication(sys.argv[:1])
    client = KeckogecoClient(args.url, token=args.token)
    try:
        client.health()
    except Exception as exc:  # noqa: BLE001 - present any startup failure
        QMessageBox.critical(
            None,
            "keckogeco",
            f"Cannot reach the server at {args.url}:\n{exc}\n\n"
            "Start one with:  keckogeco-server   (or keckogeco-server --sim)",
        )
        return 1
    window = MainWindow(client)
    window.resize(1100, 750)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
