"""Logging configuration for keckogeco applications.

Library modules never configure logging themselves — they only call
``logging.getLogger(__name__)``. The console entry points (server, GUI,
check, find) call :func:`setup_logging` once at startup, which attaches a
console handler and a size-rotating file handler.
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from .config import LoggingConfig

__all__ = ["setup_logging"]

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"

# Rotation: 10 MB per file, 30 files ≈ 300 MB cap. The old system rotated at
# 1 MB and emailed each rotated file via Outlook COM; we keep them on disk.
_MAX_BYTES = 10 * 1024 * 1024
_BACKUP_COUNT = 30


def setup_logging(cfg: LoggingConfig | None = None, *, console: bool = True) -> Path:
    """Configure the root ``keckogeco`` logger. Returns the log-file path.

    Safe to call more than once; handlers are only added on the first call.
    """
    cfg = cfg or LoggingConfig()
    logger = logging.getLogger("keckogeco")
    logger.setLevel(cfg.level)
    log_dir = Path(cfg.dir)

    if logger.handlers:  # already configured (e.g. tests, repeated calls)
        return log_dir / "keckogeco.log"

    formatter = logging.Formatter(_FORMAT)

    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "keckogeco.log"
    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    if console:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    return log_file
