"""Cross-company EDGAR: curated value-chain peers' headline as-reported signals."""
from __future__ import annotations

import logging

from saturn.analytics.metrics import compute_metrics
from saturn.ingestion.edgar import _fetch_companyfacts, _parse_companyfacts
from saturn.ingestion.errors import DataUnavailable
from saturn.ingestion.identifiers import ticker_to_cik
from saturn.models import IndustryContext, PeerSummary, Provenance

logger = logging.getLogger(__name__)

_AI_COMPUTE_CHAIN = [
    ("NVDA", "demand"), ("AMD", "demand"),
    ("MSFT", "demand"), ("GOOGL", "demand"), ("AMZN", "demand"), ("META", "demand"),
    ("AMAT", "supply"), ("LRCX", "supply"),
]
VALUE_CHAIN = {"semiconductor": _AI_COMPUTE_CHAIN}
# Ticker fallback: the target's industry isn't always populated (identity may be absent),
# so map known semiconductor/AI-compute names straight to the chain by ticker.
_SEMI_TICKERS = {"MU", "NVDA", "AMD", "INTC", "AVGO", "TSM", "QCOM", "TXN", "MRVL",
                 "AMAT", "LRCX", "KLAC", "ASML", "SMCI", "ARM", "WDC", "STX"}
_NOTE = ("US-filer value-chain proxies (revenue/capex); excludes foreign filers "
         "(e.g. TSMC/ASML, IFRS) and does not include GPU-unit or HBM-content estimates.")


def _peers_for(industry: str | None, ticker: str | None = None) -> list[tuple[str, str]]:
    key = (industry or "").lower()
    for kw, chain in VALUE_CHAIN.items():
        if kw in key:
            return chain
    if ticker and ticker.upper() in _SEMI_TICKERS:
        return _AI_COMPUTE_CHAIN
    return []


def _period_rank(period: str) -> tuple[int, int]:
    p = period or ""
    if p.startswith("FY"):
        try:
            return (int(p[2:]), 4)
        except ValueError:
            return (-1, -1)
    try:
        q, fy = p.split()
        return (int(fy[2:]), int(q[1]))
    except (ValueError, IndexError):
        return (-1, -1)


def _latest_metric(ms, name):
    xs = [m for m in ms if m.name == name]
    return max(xs, key=lambda m: _period_rank(m.fiscal_period)).value if xs else None


def _latest_capex(facts):
    xs = [f for f in facts if f.concept == "CapitalExpenditures" and f.value is not None]
    return max(xs, key=lambda f: _period_rank(f.fiscal_period)).value if xs else None


def _peer_summary(ticker: str, role: str) -> PeerSummary | None:
    try:
        fund = _parse_companyfacts(_fetch_companyfacts(ticker_to_cik(ticker)))
        ms = compute_metrics(fund, None)
        return PeerSummary(
            ticker=ticker, role=role,
            revenue_ttm=_latest_metric(ms, "revenue_ttm"),
            revenue_growth_yoy=_latest_metric(ms, "revenue_growth_yoy"),
            capex=_latest_capex(fund.facts),
            capex_intensity=_latest_metric(ms, "capex_intensity"),
            provenance=Provenance(source="SEC EDGAR"),
        )
    except Exception as exc:  # noqa: BLE001 - a peer is optional
        logger.debug("peer %s unavailable: %s", ticker, exc)
        return None


def fetch_industry_context(target_ticker: str, industry: str | None) -> IndustryContext:
    peers = []
    for tk, role in _peers_for(industry, target_ticker):
        if tk.upper() == (target_ticker or "").upper():
            continue
        s = _peer_summary(tk, role)
        if s:
            peers.append(s)
    if not peers:
        raise DataUnavailable(f"no value-chain peers for {target_ticker} (industry {industry!r})")
    return IndustryContext(peers=peers, note=_NOTE, provenance=Provenance(source="SEC EDGAR"))
