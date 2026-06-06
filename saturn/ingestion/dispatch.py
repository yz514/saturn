"""Soft-fail dispatch for ingestion sources.

A source is just a zero-arg callable that returns a canonical object or raises.
`route_to_source` converts any failure into a recorded `SourceGap` so a single
flaky source never crashes the whole dossier — adopted from TradingAgents'
route_to_vendor pattern, adapted to a (result, gap) return.
"""

from __future__ import annotations

import logging
from typing import Callable, TypeVar

from saturn.ingestion.errors import IngestionError
from saturn.models import SourceGap

logger = logging.getLogger(__name__)

T = TypeVar("T")


def route_to_source(
    source: str, fetch: Callable[[], T]
) -> tuple[T | None, SourceGap | None]:
    """Call `fetch`; return (result, None) on success or (None, gap) on failure."""
    try:
        return fetch(), None
    except IngestionError as exc:
        logger.warning("source %s unavailable: %s", source, exc)
        return None, SourceGap(source=source, reason=str(exc))
    except Exception as exc:  # noqa: BLE001 - never let one source crash the run
        logger.warning("source %s errored: %s", source, exc)
        return None, SourceGap(source=source, reason=f"{type(exc).__name__}: {exc}")
