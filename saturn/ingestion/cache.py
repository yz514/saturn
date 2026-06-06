"""Tiny per-source disk cache for ingestion payloads.

Entries are JSON files under `<root>/<source>/<key>_<YYYY-MM-DD>.json`. A read is
a hit only if a file exists whose date stamp is within `ttl_days` of `today`.
The newest in-window file wins. Dates are injected (never read from the clock
here) so the behaviour is deterministic and testable.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_ROOT = Path("data/cache")


def _dir(source: str, root: Path) -> Path:
    return root / source


def write_cache(
    source: str,
    key: str,
    payload: object,
    *,
    root: Path = DEFAULT_ROOT,
    today: date,
) -> Path:
    """Write `payload` as JSON and return the path written."""
    d = _dir(source, root)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{key}_{today:%Y-%m-%d}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info("cache write: %s", path)
    return path


def read_cache(
    source: str,
    key: str,
    *,
    ttl_days: int,
    root: Path = DEFAULT_ROOT,
    today: date,
) -> object | None:
    """Return the freshest cached payload within TTL, or None on miss."""
    if ttl_days < 0:
        raise ValueError("ttl_days must be >= 0")
    d = _dir(source, root)
    if not d.exists():
        return None
    best_date: date | None = None
    best_path: Path | None = None
    prefix = f"{key}_"
    for path in d.glob(f"{key}_*.json"):
        stamp = path.stem[len(prefix):]
        try:
            stamp_date = date.fromisoformat(stamp)
        except ValueError:
            continue
        age = (today - stamp_date).days
        if 0 <= age <= ttl_days and (best_date is None or stamp_date > best_date):
            best_date, best_path = stamp_date, path
    if best_path is None:
        return None
    logger.info("cache hit: %s", best_path)
    return json.loads(best_path.read_text(encoding="utf-8"))
