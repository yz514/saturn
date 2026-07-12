"""Typed data models shared across the Saturn pipeline."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class NewsItem(BaseModel):
    title: str
    publisher: str | None = None
    link: str | None = None
    published: str | None = None


class Provenance(BaseModel):
    """Lineage for a single datum: where it came from and when."""

    source: str
    source_url: str | None = None
    as_of: date | None = None
    retrieved_at: date | None = None


class Quote(BaseModel):
    price: float | None = None
    market_cap: float | None = None
    currency: str | None = None
    provenance: Provenance


class FinancialFact(BaseModel):
    concept: str
    value: float | None = None
    unit: str | None = None
    fiscal_period: str | None = None
    provenance: Provenance


class Fundamentals(BaseModel):
    facts: list[FinancialFact] = Field(default_factory=list)


class FilingSection(BaseModel):
    name: str
    excerpt: str
    full_text_cache_ref: str | None = None
    provenance: Provenance


class MacroSeries(BaseModel):
    series_id: str
    title: str
    observations: list[tuple[date, float]] = Field(default_factory=list)
    provenance: Provenance


class MacroSnapshot(BaseModel):
    series: list[MacroSeries] = Field(default_factory=list)


class ConsensusSnapshot(BaseModel):
    """Validated, best-effort analyst consensus (yfinance). A distinct epistemic
    class: external estimate data, not as-reported and not a Saturn model output."""

    forward_eps: float | None = None
    forward_pe: float | None = None
    peg: float | None = None
    target_mean: float | None = None
    target_high: float | None = None
    target_low: float | None = None
    target_upside_pct: float | None = None
    rating: str | None = None
    n_analysts: int | None = None
    last_eps_surprise_pct: float | None = None
    provenance: Provenance
    rejected: list[str] = Field(default_factory=list)


class MaterialEvent(BaseModel):
    """A single SEC 8-K filing (material event), optionally with a body excerpt."""

    form: str = "8-K"
    filing_date: date
    item_codes: list[str] = Field(default_factory=list)
    title: str | None = None
    excerpt: str | None = None
    full_text_cache_ref: str | None = None
    provenance: Provenance


class MetricInput(BaseModel):
    """One source fact a derived metric consumed (for verification)."""

    concept: str
    fiscal_period: str | None = None
    value: float
    source: str


class DerivedMetric(BaseModel):
    """A deterministically computed metric carrying its formula and inputs."""

    name: str
    value: float
    format: str  # percent | ratio | currency | x | per_share
    fiscal_period: str | None = None
    formula: str
    inputs: list[MetricInput] = Field(default_factory=list)
    provenance: Provenance


class SourceGap(BaseModel):
    """A source that could not contribute, recorded instead of crashing."""

    source: str
    reason: str


class PeerSummary(BaseModel):
    """One value-chain peer's headline as-reported signals (demand/supply proxy)."""
    ticker: str
    role: str                       # demand | supply | peer
    name: str | None = None
    revenue_ttm: float | None = None
    revenue_growth_yoy: float | None = None
    capex: float | None = None
    capex_intensity: float | None = None
    provenance: Provenance


class IndustryContext(BaseModel):
    peers: list[PeerSummary] = Field(default_factory=list)
    note: str = ""
    provenance: Provenance


class ExpectationAnchor(BaseModel):
    """What the market is pricing in — the base the variant view is measured against."""
    source: Literal["consensus", "reverse_dcf_implied", "none"]
    metric: str | None = None
    period: str | None = None
    value: float | None = None
    unit: str | None = None
    text: str
    confidence: Literal["high", "medium", "low"]


class ScenarioLeg(BaseModel):
    """One bull/base/bear leg. The LLM supplies the assumption; code computes the price."""
    name: Literal["bull", "base", "bear"]
    period: str
    driver: str
    metric: Literal["EPS", "FCF/share", "sales/share"]
    metric_basis: Literal["GAAP", "non_GAAP", "adjusted", "cycle_normalized"]
    per_share_value: float
    multiple: float
    multiple_basis: Literal["P/E", "P/FCF", "P/S"]
    implied_price: float | None = None
    implied_return_pct: float | None = None


class AlphaThesis(BaseModel):
    """A tradeable variant view: anchor, stance, falsifier, and priced scenarios.
    LLM-supplied fields default so a partial LLM response still validates; the
    completeness gate flags the gaps."""
    anchor: ExpectationAnchor
    stance: Literal["above_consensus", "in_line_consensus", "below_consensus", "unclear"] = "unclear"
    stance_basis: str = ""   # human note on how stance was derived (or that it was LLM-declared)
    variant: str = ""
    rationale: str = ""
    confidence: Literal["high", "medium", "low"] = "low"
    key_variable: str = ""
    falsifier: str = ""
    horizon: str = ""
    scenarios: list[ScenarioLeg] = Field(default_factory=list)
    incompleteness: list[str] = Field(default_factory=list)
    provenance: Provenance


class CompanyDossier(BaseModel):
    """Rich, provenance-tagged evidence envelope consumed by the agents."""

    ticker: str
    cik: str | None = None
    name: str
    sector: str | None = None
    industry: str | None = None
    business_summary: str | None = None
    segments: list[str] = Field(default_factory=list)
    quote: Quote | None = None
    fundamentals: Fundamentals | None = None
    filing_sections: list[FilingSection] = Field(default_factory=list)
    material_events: list[MaterialEvent] = Field(default_factory=list)
    derived_metrics: list[DerivedMetric] = Field(default_factory=list)
    macro: MacroSnapshot | None = None
    consensus: ConsensusSnapshot | None = None
    news: list[NewsItem] = Field(default_factory=list)
    gaps: list[SourceGap] = Field(default_factory=list)
    generated_at: date
    industry_context: IndustryContext | None = None


class CompanyData(BaseModel):
    """Structured company facts produced by ingestion (real or mock)."""

    ticker: str
    name: str
    sector: str | None = None
    industry: str | None = None
    business_summary: str | None = None
    segments: list[str] = Field(default_factory=list)
    price: float | None = None
    currency: str | None = None
    market_cap: float | None = None
    metrics: dict[str, float | None] = Field(default_factory=dict)
    news: list[NewsItem] = Field(default_factory=list)
    as_of: date


class AnalysisSections(BaseModel):
    """Reasoned sections produced by the `analyze` LLM call."""

    executive_summary: str
    company_overview: str
    business_segments: str
    financial_snapshot: str
    valuation_discussion: str
    key_risks: str
    open_questions: str


class DebateSections(BaseModel):
    """Bull/bear/synthesis produced by the `debate` LLM call."""

    bull_thesis: str
    bear_thesis: str
    final_view: str


class CriticFinding(BaseModel):
    """One issue the Critic found: a report claim not supported by the data.
    Fields default so a finding missing one (imperfect LLM JSON) still validates
    rather than discarding the whole review."""
    claim: str = ""
    section: str = ""
    category: str = "unverified_claim"   # unsupported_number | contradiction | over_weighting | unverified_claim
    verdict: str = ""                     # contradicted | unsupported | flagged
    evidence: str = ""
    severity: str = "medium"              # high | medium | low


class CriticReview(BaseModel):
    """Advisory verification of the drafted report against the dossier."""
    findings: list[CriticFinding] = Field(default_factory=list)
    claims_checked: int = 0
    summary: str = ""
    repaired: bool = False   # True when the self-repair loop corrected the draft
    provenance: Provenance


class ResearchReport(BaseModel):
    """The fully-composed research report, ready to render."""

    ticker: str
    company: CompanyDossier
    analysis: AnalysisSections
    debate: DebateSections
    generated_at: date
    model_used: str
    mock: bool
    sources: list[str] = Field(default_factory=list)
    critic_review: CriticReview | None = None
    alpha_thesis: AlphaThesis | None = None
