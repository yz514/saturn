"""Sequential equity-research pipeline: analyze -> debate -> assemble."""

from __future__ import annotations

import logging
from datetime import date

from saturn.llm.base import LLMClient
from saturn.models import (
    AnalysisSections,
    CompanyData,
    DebateSections,
    ResearchReport,
)

logger = logging.getLogger(__name__)

ANALYSIS_SYSTEM = (
    "You are a rigorous buy-side equity research analyst. Base every statement "
    "only on the provided company data. Do not invent figures. Be concise and "
    "balanced. Respond with ONLY a valid JSON object, no prose, no code fences."
)

DEBATE_SYSTEM = (
    "You run a structured bull/bear debate for an equity. Build the strongest "
    "honest case for each side from the provided data, then a balanced final "
    "view. Respond with ONLY a valid JSON object, no prose, no code fences."
)


def _company_context(company: CompanyData) -> str:
    return company.model_dump_json(indent=2)


def _extract_json(text: str) -> str:
    """Strip surrounding ```/```json code fences if present."""
    t = text.strip()
    if t.startswith("```"):
        lines = t.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    return t


def analyze(
    company: CompanyData, llm: LLMClient, *, model: str | None = None
) -> AnalysisSections:
    prompt = (
        "OUTPUT_SCHEMA=analysis\n"
        f"Company data (JSON):\n{_company_context(company)}\n\n"
        "Return ONLY a JSON object with these string keys: "
        "executive_summary, company_overview, business_segments, "
        "financial_snapshot, valuation_discussion, key_risks, open_questions."
    )
    logger.info("analyze: %s", company.ticker)
    raw = llm.complete(ANALYSIS_SYSTEM, prompt, model=model)
    return AnalysisSections.model_validate_json(_extract_json(raw))


def debate(
    company: CompanyData, llm: LLMClient, *, model: str | None = None
) -> DebateSections:
    prompt = (
        "OUTPUT_SCHEMA=debate\n"
        f"Company data (JSON):\n{_company_context(company)}\n\n"
        "Return ONLY a JSON object with these string keys: "
        "bull_thesis, bear_thesis, final_view."
    )
    logger.info("debate: %s", company.ticker)
    raw = llm.complete(DEBATE_SYSTEM, prompt, model=model)
    return DebateSections.model_validate_json(_extract_json(raw))


def _build_sources(company: CompanyData, *, mock: bool) -> list[str]:
    if mock:
        return ["MOCK fixture data — not real market sources"]
    sources = ["yfinance (price, profile, financials)"]
    sources += [item.link for item in company.news if item.link]
    return sources


def run(
    company: CompanyData,
    llm: LLMClient,
    *,
    model_used: str,
    mock: bool,
) -> ResearchReport:
    """Run the full pipeline and return an assembled ResearchReport."""
    call_model = None if mock else model_used
    analysis = analyze(company, llm, model=call_model)
    deb = debate(company, llm, model=call_model)
    return ResearchReport(
        ticker=company.ticker,
        company=company,
        analysis=analysis,
        debate=deb,
        generated_at=date.today(),
        model_used=model_used,
        mock=mock,
        sources=_build_sources(company, mock=mock),
    )
