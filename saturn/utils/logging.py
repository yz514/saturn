"""Structured logging configuration."""

from __future__ import annotations

import logging


def setup_logging(level: str = "INFO") -> None:
    """Configure root logging. Unknown levels fall back to INFO."""
    resolved = getattr(logging, level.upper(), logging.INFO)
    if not isinstance(resolved, int):
        resolved = logging.INFO
    logging.basicConfig(
        level=resolved,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=True,
    )
