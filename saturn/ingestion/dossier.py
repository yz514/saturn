"""Assemble a CompanyDossier from source adapters via the dispatcher.

Slice-1 framework: the quote adapter (yfinance) is wired for real. EDGAR and
FRED are passed in as optional callables; until their plans land they default to
None and the dispatcher records a gap. This keeps the orchestration shape stable
while real adapters are added incrementally.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Callable

from saturn.analytics.driver import compute_driver_model
from saturn.analytics.forward import compute_forward
from saturn.analytics.metrics import compute_metrics
from saturn.ingestion.consensus import fetch_consensus, validate_consensus, RawConsensus
from saturn.ingestion.dispatch import route_to_source
from saturn.ingestion.edgar import fetch_edgar
from saturn.ingestion.errors import DataUnavailable
from saturn.ingestion.fred import fetch_fred
from saturn.ingestion.peers import fetch_industry_context
from saturn.ingestion.prices import fetch_company_data, fetch_quote
from saturn.models import (
    CompanyDossier,
    ConsensusSnapshot,
    FilingSection,
    FinancialFact,
    Fundamentals,
    IndustryContext,
    MacroSeries,
    MacroSnapshot,
    MaterialEvent,
    NewsItem,
    PeerSummary,
    Provenance,
    Quote,
)

logger = logging.getLogger(__name__)


def _mock_dossier(ticker: str) -> CompanyDossier:
    prov_q = Provenance(source="yfinance (mock)", as_of=date.today())
    prov_e = Provenance(
        source="SEC EDGAR (mock)",
        source_url="https://www.sec.gov/",
        as_of=date(2025, 2, 21),
    )
    prov_f = Provenance(source="FRED (mock)", as_of=date(2026, 5, 1))
    dossier = CompanyDossier(
        ticker=ticker,
        cik="0001045810",
        name="NVIDIA Corporation",
        sector="Technology",
        industry="Semiconductors",
        business_summary="[MOCK] Designs GPUs and accelerated computing platforms.",
        segments=["Data Center", "Gaming", "Professional Visualization", "Automotive"],
        quote=Quote(price=900.0, market_cap=2_200_000_000_000.0, currency="USD", provenance=prov_q),
        fundamentals=Fundamentals(
            facts=[
                FinancialFact(concept="Revenues", value=60_900_000_000.0, unit="USD", fiscal_period="FY2024", provenance=prov_e),
                FinancialFact(concept="NetIncomeLoss", value=29_760_000_000.0, unit="USD", fiscal_period="FY2024", provenance=prov_e),
                FinancialFact(concept="WeightedAverageSharesDiluted", value=24_640_000_000.0, unit="shares", fiscal_period="FY2024", provenance=prov_e),
                FinancialFact(concept="Revenues", value=26_970_000_000.0, unit="USD", fiscal_period="FY2023", provenance=prov_e),
                FinancialFact(concept="Revenues", value=30_040_000_000.0, unit="USD", fiscal_period="Q2 FY2025", provenance=prov_e),
            ]
        ),
        filing_sections=[
            FilingSection(
                name="Risk Factors",
                excerpt="[MOCK] Demand for our products may not meet expectations; supply is concentrated.",
                provenance=prov_e,
            )
        ],
        material_events=[
            MaterialEvent(
                filing_date=date(2024, 5, 22),
                item_codes=["2.02", "9.01"],
                title="Results of Operations and Financial Condition",
                excerpt="[MOCK] Reported record quarterly revenue.",
                provenance=prov_e,
            )
        ],
        macro=MacroSnapshot(
            series=[
                MacroSeries(
                    series_id="FEDFUNDS",
                    title="Federal Funds Effective Rate",
                    observations=[(date(2026, 4, 1), 4.33)],
                    provenance=prov_f,
                )
            ]
        ),
        news=[NewsItem(title="[MOCK] NVIDIA announces next-gen architecture", publisher="MockWire", link="https://example.com/mock")],
        generated_at=date.today(),
    )
    dossier.derived_metrics = compute_metrics(dossier.fundamentals, dossier.quote) + compute_forward(dossier.fundamentals, dossier.quote)
    dossier.consensus = ConsensusSnapshot(
        forward_eps=32.0, forward_pe=28.0, peg=1.5,
        target_mean=1000.0, target_high=1200.0, target_low=800.0, target_upside_pct=1000.0 / 900.0 - 1,
        rating="buy", n_analysts=40, last_eps_surprise_pct=0.05,
        provenance=Provenance(source="yfinance (estimate, mock)", as_of=date.today()),
    )
    dossier.driver_model = compute_driver_model(dossier.fundamentals, dossier.quote, dossier.consensus)
    prov_ic = Provenance(source="SEC EDGAR (mock)")
    dossier.industry_context = IndustryContext(
        peers=[
            PeerSummary(ticker="NVDA", role="demand", revenue_growth_yoy=1.22, capex=11_000_000_000.0, provenance=prov_ic),
            PeerSummary(ticker="MSFT", role="demand", revenue_growth_yoy=0.17, capex=44_000_000_000.0, provenance=prov_ic),
        ],
        note="[MOCK] US-filer value-chain proxies (revenue/capex).",
        provenance=prov_ic,
    )
    return dossier


def build_dossier(
    ticker: str,
    *,
    mock: bool = False,
    quote_fn: Callable[..., Quote] = fetch_quote,
    edgar_fn: Callable[..., object] | None = fetch_edgar,
    fred_fn: Callable[..., object] | None = fetch_fred,
    identity: dict | None = None,
    identity_fn: Callable[..., object] | None = None,
) -> CompanyDossier:
    """Build a CompanyDossier. mock=True returns the offline fixture.

    Adapter contracts:
    - quote_fn(ticker, *, mock) -> Quote
    - edgar_fn(ticker) -> dict with keys "fundamentals" (Fundamentals),
      "filing_sections" (list[FilingSection]), "material_events"
      (list[MaterialEvent]), "name", and "cik"
    - fred_fn(ticker) -> MacroSnapshot

    edgar_fn/fred_fn are injected by later plans; when None, the dispatcher
    records a gap for that source. Any adapter that raises is recorded as a
    gap rather than crashing the build.
    """
    if mock:
        logger.info("dossier(mock): %s", ticker)
        return _mock_dossier(ticker)

    ident = identity or {}
    gaps = []

    # Populate identity (sector/industry/business_summary/segments/news) from yfinance when
    # not supplied — the CLI calls build_dossier(ticker) with no identity, so without this
    # the dossier's industry/sector/etc. are all None in real runs.
    if identity is None:
        fn = identity_fn or fetch_company_data   # module global, so it's monkeypatchable
        def _identity():
            cd = fn(ticker, mock=False)
            return {
                "name": cd.name, "sector": cd.sector, "industry": cd.industry,
                "business_summary": cd.business_summary, "segments": cd.segments, "news": cd.news,
            }
        id_result, gap = route_to_source("identity", _identity)
        if gap:
            gaps.append(gap)
        if isinstance(id_result, dict):
            ident = id_result

    quote, gap = route_to_source("quote", lambda: quote_fn(ticker, mock=False))
    if gap:
        gaps.append(gap)

    def _edgar():
        if edgar_fn is None:
            raise DataUnavailable("edgar adapter not configured")
        return edgar_fn(ticker)

    edgar_result, gap = route_to_source("edgar", _edgar)
    if gap:
        gaps.append(gap)

    def _fred():
        if fred_fn is None:
            raise DataUnavailable("fred adapter not configured")
        return fred_fn(ticker)

    fred_result, gap = route_to_source("fred", _fred)
    if gap:
        gaps.append(gap)

    fundamentals = filing_sections = None
    edgar_name = edgar_cik = None
    material_events: list = []
    if isinstance(edgar_result, dict):
        fundamentals = edgar_result.get("fundamentals")
        filing_sections = edgar_result.get("filing_sections")
        edgar_name = edgar_result.get("name")
        edgar_cik = edgar_result.get("cik")
        material_events = edgar_result.get("material_events") or []
    elif edgar_result is not None:
        logger.warning(
            "edgar adapter returned %s, expected dict with "
            "'fundamentals'/'filing_sections' keys; ignoring",
            type(edgar_result).__name__,
        )

    def _consensus():
        return fetch_consensus(ticker)

    raw_consensus, gap = route_to_source("consensus", _consensus)
    if gap:
        gaps.append(gap)
    consensus = (
        validate_consensus(raw_consensus, fundamentals, quote)
        if isinstance(raw_consensus, RawConsensus)
        else None
    )

    def _industry():
        return fetch_industry_context(ticker, ident.get("industry"))
    industry_ctx, gap = route_to_source("industry", _industry)
    if gap:
        gaps.append(gap)

    dossier = CompanyDossier(
        ticker=ticker,
        cik=ident.get("cik") or edgar_cik,
        name=ident.get("name") or edgar_name or ticker,
        sector=ident.get("sector"),
        industry=ident.get("industry"),
        business_summary=ident.get("business_summary"),
        segments=ident.get("segments", []),
        quote=quote,
        fundamentals=fundamentals,
        filing_sections=filing_sections or [],
        material_events=material_events,
        macro=fred_result if isinstance(fred_result, MacroSnapshot) else None,
        consensus=consensus,
        industry_context=industry_ctx if isinstance(industry_ctx, IndustryContext) else None,
        news=ident.get("news", []),
        gaps=gaps,
        generated_at=date.today(),
    )
    dossier.derived_metrics = compute_metrics(dossier.fundamentals, dossier.quote) + compute_forward(dossier.fundamentals, dossier.quote)
    dossier.driver_model = compute_driver_model(dossier.fundamentals, dossier.quote, dossier.consensus)
    return dossier
