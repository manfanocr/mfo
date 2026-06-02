"""Structured logging configuration for the CLI.

A single stderr handler with a consistent, parseable format. Configuration is idempotent so
repeated CLI invocations (or tests) don't attach duplicate handlers.
"""

from __future__ import annotations

import logging
import sys

_ROOT = "mfo"
_configured = False


def configure_logging(level: str = "INFO") -> None:
    """Attach a stderr handler to the ``mfo`` logger and set its level."""
    global _configured
    logger = logging.getLogger(_ROOT)
    logger.setLevel(level.upper())
    if not _configured:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
            )
        )
        logger.addHandler(handler)
        logger.propagate = False
        _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced child logger, e.g. ``get_logger("cli")`` → ``mfo.cli``."""
    return logging.getLogger(f"{_ROOT}.{name}")
