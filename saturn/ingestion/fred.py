"""FRED macro adapter: a curated set of macro series with provenance.

Macro is ticker-agnostic — fetch_fred accepts and ignores a ticker so it matches
the fred_fn(ticker) call site in build_dossier. Series titles are hardcoded in the
registry to avoid an extra metadata round-trip per series.
"""

from __future__ import annotations

import logging
from datetime import date

from saturn.models import MacroSeries, MacroSnapshot, Provenance

logger = logging.getLogger(__name__)

# Curated macro series: (series_id, human title). Spec §3 default set.
FRED_SERIES: list[tuple[str, str]] = [
    ("FEDFUNDS", "Federal Funds Effective Rate"),
    ("CPIAUCSL", "Consumer Price Index (All Urban Consumers)"),
    ("PPIACO", "Producer Price Index (All Commodities)"),
    ("DGS10", "10-Year Treasury Yield"),
    ("DGS2", "2-Year Treasury Yield"),
    ("UNRATE", "Unemployment Rate"),
    ("M2SL", "M2 Money Supply"),
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
