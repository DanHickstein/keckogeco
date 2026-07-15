"""Launch the engineering GUI.

Run with ``python -m keckogeco.gui.app``, or just open this file and press
Run in VSCode. Talks to a running server (``keckogeco/server/app.py``;
start it with ``--sim`` for an offline layout check).
"""

from __future__ import annotations

import argparse
import sys

if __package__ in (None, ""):  # run as a bare file (VSCode Run button)
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

__all__ = ["main"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="keckogeco engineering GUI")
    parser.add_argument(
        "--url", default="http://localhost:8000", help="python -m keckogeco.server.app base URL"
    )
    parser.add_argument("--token", default="", help="API bearer token, if the server uses one")
    args = parser.parse_args(argv)

    from PyQt6.QtWidgets import QApplication, QMessageBox

    from keckogeco.gui.client import KeckogecoClient
    from keckogeco.gui.mainwindow import MainWindow
    from keckogeco.gui.theme import apply_dark_theme

    app = QApplication(sys.argv[:1])
    apply_dark_theme(app)
    client = KeckogecoClient(args.url, token=args.token)
    try:
        client.health()
    except Exception as exc:  # noqa: BLE001 - present any startup failure
        QMessageBox.critical(
            None,
            "keckogeco",
            f"Cannot reach the server at {args.url}:\n{exc}\n\n"
            "Start one with:  python -m keckogeco.server.app   (or python -m keckogeco.server.app --sim)",
        )
        return 1
    window = MainWindow(client)
    window.resize(880, 780)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
