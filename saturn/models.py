"""Typed data models shared across the Saturn pipeline."""

from __future__ import annotations

from datetime import date

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


class MaterialEvent(BaseModel):
    """A single SEC 8-K filing (material event), optionally with a body excerpt."""

    form: str = "8-K"
    filing_date: date
    item_codes: list[str] = Field(default_factory=list)
    title: str | None = None
    excerpt: str | None = None
    full_text_cache_ref: str | None = None
    provenance: Provenance


class SourceGap(BaseModel):
    """A source that could not contribute, recorded instead of crashing."""

    source: str
    reason: str


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
    macro: MacroSnapshot | None = None
    news: list[NewsItem] = Field(default_factory=list)
    gaps: list[SourceGap] = Field(default_factory=list)
    generated_at: date


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
