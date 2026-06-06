"""Company data ingestion from yfinance, with an offline mock fixture."""

from __future__ import annotations

import logging
from datetime import date

from saturn.models import CompanyData, NewsItem, Provenance, Quote
from saturn.ingestion.errors import IngestionError, SourceFailure

logger = logging.getLogger(__name__)


def _mock_company(ticker: str) -> CompanyData:
    # Phase 0 fixture: always NVIDIA-shaped data, with only the ticker string
    # echoed back. A report for another ticker will show NVIDIA figures — this
    # is intentional offline sample data, replaced by real per-ticker ingestion
    # in later phases.
    return CompanyData(
        ticker=ticker,
        name="NVIDIA Corporation",
        sector="Technology",
        industry="Semiconductors",
        business_summary="[MOCK] Designs GPUs and accelerated computing platforms.",
        segments=["Data Center", "Gaming", "Professional Visualization", "Automotive"],
        price=900.0,
        currency="USD",
        market_cap=2_200_000_000_000,
        metrics={
            "trailing_pe": 65.0,
            "revenue_growth": 1.2,
            "profit_margin": 0.48,
            "free_cashflow": 27_000_000_000.0,
        },
        news=[
            NewsItem(
                title="[MOCK] NVIDIA announces next-gen architecture",
                publisher="MockWire",
                link="https://example.com/mock",
            )
        ],
        as_of=date.today(),
    )


def _extract_news(raw_news: list) -> list[NewsItem]:
    items: list[NewsItem] = []
    for entry in (raw_news or [])[:5]:
        content = entry.get("content", entry) if isinstance(entry, dict) else {}
        provider = content.get("provider")
        canonical = content.get("canonicalUrl")
        items.append(
            NewsItem(
                title=content.get("title") or entry.get("title") or "Untitled",
                publisher=(
                    provider.get("displayName")
                    if isinstance(provider, dict)
                    else entry.get("publisher")
                ),
                link=(
                    canonical.get("url")
                    if isinstance(canonical, dict)
                    else entry.get("link")
                ),
            )
        )
    return items


def fetch_company_data(ticker: str, *, mock: bool = False) -> CompanyData:
    """Return CompanyData for `ticker`. Use mock=True for offline fixture data."""
    if mock:
        logger.info("ingest(mock): %s", ticker)
        return _mock_company(ticker)

    logger.info("ingest(yfinance): %s", ticker)
    try:
        import yfinance as yf

        handle = yf.Ticker(ticker)
        info = handle.info or {}
    except Exception as exc:  # noqa: BLE001 - surface as a typed error
        raise IngestionError(
            f"Could not fetch data for {ticker}. Check the ticker or run with --mock."
        ) from exc

    if not (info.get("shortName") or info.get("longName") or info.get("symbol")):
        raise IngestionError(
            f"Could not fetch data for {ticker}. Check the ticker or run with --mock."
        )

    try:
        raw_news = handle.news
    except Exception:  # noqa: BLE001 - news is best-effort
        raw_news = []

    return CompanyData(
        ticker=ticker,
        name=info.get("longName") or info.get("shortName") or ticker,
        sector=info.get("sector"),
        industry=info.get("industry"),
        business_summary=info.get("longBusinessSummary"),
        segments=[],
        price=info.get("currentPrice") or info.get("regularMarketPrice"),
        currency=info.get("currency"),
        market_cap=info.get("marketCap"),
        metrics={
            "trailing_pe": info.get("trailingPE"),
            "forward_pe": info.get("forwardPE"),
            "revenue_growth": info.get("revenueGrowth"),
            "profit_margin": info.get("profitMargins"),
            "free_cashflow": info.get("freeCashflow"),
            "total_debt": info.get("totalDebt"),
        },
        news=_extract_news(raw_news),
        as_of=date.today(),
    )


def _mock_quote(ticker: str) -> Quote:
    return Quote(
        price=900.0,
        market_cap=2_200_000_000_000.0,
        currency="USD",
        provenance=Provenance(source="yfinance (mock)", as_of=date.today()),
    )


def fetch_quote(ticker: str, *, mock: bool = False) -> Quote:
    """Return a canonical Quote for `ticker`. mock=True for offline fixture."""
    if mock:
        logger.info("quote(mock): %s", ticker)
        return _mock_quote(ticker)

    logger.info("quote(yfinance): %s", ticker)
    try:
        import yfinance as yf

        info = (yf.Ticker(ticker).info) or {}
    except Exception as exc:  # noqa: BLE001 - surface as a typed error
        raise SourceFailure(f"yfinance quote failed for {ticker}") from exc

    return Quote(
        price=info.get("currentPrice") or info.get("regularMarketPrice"),
        market_cap=info.get("marketCap"),
        currency=info.get("currency"),
        provenance=Provenance(
            source="yfinance",
            source_url=f"https://finance.yahoo.com/quote/{ticker}",
            retrieved_at=date.today(),
        ),
    )
