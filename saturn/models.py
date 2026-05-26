"""Typed data models shared across the Saturn pipeline."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class NewsItem(BaseModel):
    title: str
    publisher: str | None = None
    link: str | None = None
    published: str | None = None


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
    company: CompanyData
    analysis: AnalysisSections
    debate: DebateSections
    generated_at: date
    model_used: str
    mock: bool
    sources: list[str] = Field(default_factory=list)
