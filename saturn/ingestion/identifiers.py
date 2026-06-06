"""Cross-source identifier resolution (centralized per design §3a).

Today: ticker -> 10-digit zero-padded CIK via SEC's company_tickers.json. The
fetch is injectable so the resolver is unit-tested offline against a fixture.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Callable

from saturn.config import get_settings
from saturn.ingestion.cache import read_cache, write_cache
from saturn.ingestion.errors import DataUnavailable
from saturn.ingestion.http import http_get

logger = logging.getLogger(__name__)

_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_TICKERS_TTL_DAYS = 30


def _parse_company_tickers(raw: dict) -> dict[str, str]:
    """Map upper-cased ticker -> 10-digit zero-padded CIK string."""
    mapping: dict[str, str] = {}
    for entry in raw.values():
        ticker = str(entry.get("ticker", "")).upper()
        cik_str = entry.get("cik_str")
        if ticker and cik_str is not None:
            mapping[ticker] = f"{int(cik_str):010d}"
    return mapping


def _default_fetch() -> dict:
    """Live fetch of company_tickers.json, cached per _TICKERS_TTL_DAYS."""
    settings = get_settings()
    cached = read_cache("edgar", "company_tickers", ttl_days=_TICKERS_TTL_DAYS, today=date.today())
    if cached is not None:
        return cached
    if not settings.sec_user_agent:
        raise DataUnavailable("SEC_USER_AGENT not set; required for SEC EDGAR access")
    raw = json.loads(http_get(_TICKERS_URL, user_agent=settings.sec_user_agent, accept="application/json"))
    write_cache("edgar", "company_tickers", raw, today=date.today())
    return raw


def ticker_to_cik(ticker: str, *, fetch: Callable[[], dict] = _default_fetch) -> str:
    """Resolve `ticker` to a 10-digit CIK. Raises DataUnavailable if unknown."""
    mapping = _parse_company_tickers(fetch())
    cik = mapping.get(ticker.upper())
    if cik is None:
        raise DataUnavailable(f"no CIK found for ticker {ticker!r}")
    return cik
