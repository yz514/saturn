"""FRED macro adapter: a curated set of macro series with provenance.

Macro is ticker-agnostic — fetch_fred accepts and ignores a ticker so it matches
the fred_fn(ticker) call site in build_dossier. Series titles are hardcoded in the
registry to avoid an extra metadata round-trip per series.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Callable

from saturn.config import get_settings
from saturn.ingestion.errors import DataUnavailable
from saturn.ingestion.http import http_get
from saturn.models import MacroSeries, MacroSnapshot, Provenance

logger = logging.getLogger(__name__)

# Curated macro series: (series_id, human title). Spec §3 default set.
FRED_SERIES: list[tuple[str, str]] = [
    ("FEDFUNDS", "Federal Funds Effective Rate"),
    ("DGS10", "10-Year Treasury Yield"),
    ("DGS2", "2-Year Treasury Yield"),
    ("T10Y2Y", "10Y-2Y Treasury Spread"),
    ("CPIAUCSL", "Consumer Price Index (All Urban Consumers)"),
    ("CPILFESL", "Core CPI (ex Food & Energy)"),
    ("PCEPILFE", "Core PCE Price Index"),
    ("PPIACO", "Producer Price Index (All Commodities)"),
    ("GDPC1", "Real GDP"),
    ("UNRATE", "Unemployment Rate"),
    ("PAYEMS", "Nonfarm Payrolls"),
    ("M2SL", "M2 Money Supply"),
    ("BAMLH0A0HYM2", "High-Yield Credit Spread"),
    ("VIXCLS", "CBOE Volatility Index (VIX)"),
    ("DCOILWTICO", "WTI Crude Oil Price"),
    ("DTWEXBGS", "Trade-Weighted US Dollar Index"),
]

_OBS_URL = (
    "https://api.stlouisfed.org/fred/series/observations"
    "?series_id={series_id}&api_key={api_key}&file_type=json"
    "&sort_order=asc&observation_start={start}"
)


def _parse_observations(raw: dict) -> list[tuple[date, float]]:
    """Parse a FRED observations response into sorted (date, value) tuples.

    Missing values (the literal '.') are skipped. Output is ascending by date.
    """
    out: list[tuple[date, float]] = []
    for o in raw.get("observations", []):
        val = o.get("value")
        if val is None or val == ".":
            continue
        try:
            out.append((date.fromisoformat(o["date"]), float(val)))
        except (ValueError, KeyError):
            continue
    out.sort(key=lambda t: t[0])
    return out


_DEFAULT_START = "2015-01-01"  # ~10y of history is plenty for macro context


def _fetch_series_observations(series_id: str, api_key: str) -> dict:
    url = _OBS_URL.format(series_id=series_id, api_key=api_key, start=_DEFAULT_START)
    return json.loads(http_get(url, user_agent="Saturn research", accept="application/json"))


def fetch_fred(
    ticker: str | None = None,
    *,
    fetch: Callable[[str, str], dict] = _fetch_series_observations,
) -> MacroSnapshot:
    """Return a MacroSnapshot of the curated FRED series. `ticker` is ignored
    (macro is company-independent). Raises DataUnavailable if FRED_API_KEY is unset;
    SourceFailure (via http_get) on transport errors."""
    api_key = get_settings().fred_api_key
    if not api_key:
        raise DataUnavailable("FRED_API_KEY not set")

    series: list[MacroSeries] = []
    # All-or-nothing: any per-series transport error raises SourceFailure out of
    # this function, and the dispatcher records the whole "fred" source as one gap.
    # Per-series partial degradation is a deliberate future enhancement, not needed
    # while the curated list is small and stable.
    for series_id, title in FRED_SERIES:
        raw = fetch(series_id, api_key)
        obs = _parse_observations(raw)
        series.append(
            MacroSeries(
                series_id=series_id,
                title=title,
                observations=obs,
                provenance=Provenance(
                    source="FRED",
                    source_url=f"https://fred.stlouisfed.org/series/{series_id}",
                    retrieved_at=date.today(),
                ),
            )
        )
    return MacroSnapshot(series=series)
