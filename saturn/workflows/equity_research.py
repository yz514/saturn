"""Sequential equity-research pipeline: analyze -> debate -> assemble."""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import TypeVar

from pydantic import ValidationError

from saturn.analytics.forward import is_reverse_dcf_low_confidence
from saturn.agents.critic import critique
from saturn.llm.base import LLMClient
from saturn.models import (
    AnalysisSections,
    CompanyDossier,
    DebateSections,
    ResearchReport,
)

_T = TypeVar("_T")

logger = logging.getLogger(__name__)

ANALYSIS_SYSTEM = (
    "You are a rigorous buy-side equity research analyst. Base every statement "
    "only on the provided company data. Do not invent figures. Be concise and "
    "balanced. Respond with ONLY a valid JSON object, no prose, no code fences. "
    "When a \"Business Unit / Segment Results\" disclosure appears in FILING SECTIONS, "
    "render it as a markdown table (segment, revenue, gross/operating margin) and "
    "analyze the growth drivers by segment. Never state that segment data is "
    "unavailable when such a disclosure is provided. That same disclosure also carries "
    "the quarter's financial highlights (operating cash flow, adjusted free cash flow), "
    "forward guidance, and any customer-agreement details — use the latest-quarter FCF "
    "and guidance in the cash-flow and valuation discussion instead of relying only on "
    "older annual figures. When RemainingPerformanceObligation (contracted backlog / RPO) "
    "or ContractLiability (customer deposits) appear in the data, discuss them as "
    "revenue-visibility and customer-commitment signals — but note that GAAP RPO excludes "
    "non-binding long-term supply commitments (e.g. strategic customer agreements), so do "
    "not conflate it with any larger management-disclosed commitment figure."
)

DEBATE_SYSTEM = (
    "You run a structured bull/bear debate for an equity. Build the strongest "
    "honest case for each side from the provided data, then a balanced final "
    "view. Respond with ONLY a valid JSON object, no prose, no code fences."
)

_MAX_OUTPUT_TOKENS = 8192

_CTX_MAX_ANNUAL = 3
_CTX_MAX_QUARTERS = 4
_CTX_SECTION_CHARS = 1200
_CTX_EARNINGS_SECTION_CHARS = 5000
_CTX_MAX_EVENTS = 6
_CTX_EVENT_CHARS = 500
_CTX_MAX_METRIC_ANNUAL = 3
_CTX_MAX_METRIC_QUARTERS = 2


def _fy_num(period: str) -> int:
    """'FY2024' -> 2024; unparseable -> -1."""
    try:
        return int((period or "").replace("FY", "").strip())
    except (ValueError, AttributeError):
        return -1


def _q_sort(period: str) -> tuple[int, int]:
    """'Q2 FY2025' -> (2025, 2); unparseable -> (-1, -1)."""
    try:
        q_part, fy_part = period.split()
        return (int(fy_part.replace("FY", "")), int(q_part[1]))
    except (ValueError, AttributeError, IndexError):
        return (-1, -1)


def _select_context_facts(facts: list) -> list:
    """Per concept, keep the most-recent _CTX_MAX_ANNUAL annual + _CTX_MAX_QUARTERS
    quarterly facts (prompt budget control; the dossier keeps the full set)."""
    by_concept: dict[str, list] = {}
    for f in facts:
        by_concept.setdefault(f.concept, []).append(f)
    out: list = []
    for items in by_concept.values():
        annual = [x for x in items if (x.fiscal_period or "").startswith("FY")]
        quarterly = [x for x in items if (x.fiscal_period or "").startswith("Q")]
        # facts whose fiscal_period isn't FY*/Q* (e.g. TTM) are intentionally
        # excluded from the prompt context.
        annual.sort(key=lambda x: _fy_num(x.fiscal_period), reverse=True)
        quarterly.sort(key=lambda x: _q_sort(x.fiscal_period), reverse=True)
        out.extend(annual[:_CTX_MAX_ANNUAL])
        out.extend(quarterly[:_CTX_MAX_QUARTERS])
    return out


def _company_context(dossier: CompanyDossier) -> str:
    """Render the dossier as provenance-tagged text the agents can cite."""
    lines: list[str] = []
    lines.append(f"COMPANY: {dossier.name} ({dossier.ticker})")
    if dossier.cik:
        lines.append(f"CIK: {dossier.cik}")
    for label, val in (("Sector", dossier.sector), ("Industry", dossier.industry)):
        if val:
            lines.append(f"{label}: {val}")
    if dossier.business_summary:
        lines.append(f"Business summary: {dossier.business_summary}")
    if dossier.segments:
        lines.append(f"Segments: {', '.join(dossier.segments)}")

    if dossier.quote:
        q = dossier.quote
        lines.append(
            f"\nQUOTE (source: {q.provenance.source}): "
            f"price={q.price} {q.currency or ''}, market_cap={q.market_cap}"
        )

    if dossier.fundamentals and dossier.fundamentals.facts:
        lines.append("\nFUNDAMENTALS (as-reported):")
        for fact in _select_context_facts(dossier.fundamentals.facts):
            cite = fact.provenance.source
            if fact.provenance.as_of:
                cite += f", as of {fact.provenance.as_of}"
            period = fact.fiscal_period or "?"
            lines.append(
                f"- {fact.concept} {period}: {fact.value} {fact.unit or ''} (source: {cite})"
            )

    _derived = [m for m in dossier.derived_metrics if m.provenance.source != "Saturn (model)"]
    _forward = [m for m in dossier.derived_metrics if m.provenance.source == "Saturn (model)"]
    if _derived:
        lines.append("\nDERIVED METRICS (computed by Saturn from as-reported data):")
        # bound display: recent annual + quarterly per metric, plus point-in-time
        by_name: dict[str, list] = {}
        for m in _derived:
            by_name.setdefault(m.name, []).append(m)
        for name, metrics in by_name.items():
            annual = [m for m in metrics if (m.fiscal_period or "").startswith("FY")][:_CTX_MAX_METRIC_ANNUAL]
            quarterly = [m for m in metrics if (m.fiscal_period or "").startswith("Q")][:_CTX_MAX_METRIC_QUARTERS]
            other = [m for m in metrics if not (m.fiscal_period or "").startswith(("FY", "Q"))]
            for m in annual + quarterly + other:
                period = m.fiscal_period or "current"
                lines.append(
                    f"- {m.name} [{period}]: {m.value} ({m.formula}; source: Saturn derived)"
                )
    if _forward:
        lines.append("\nFORWARD / EXPECTATIONS (Saturn reverse-DCF model; assumption-dependent):")
        for m in _forward:
            lines.append(f"- {m.name}: {m.value} ({m.formula}; source: Saturn model)")
        if is_reverse_dcf_low_confidence(_forward):
            lines.append(
                "  NOTE: reverse-DCF is LOW CONFIDENCE here — the price implies FCF growth beyond the "
                "model's bounds, so the trailing FCF base is likely cycle-depressed (e.g. a capex-heavy "
                "trough year). Treat the fair value / margin of safety as a rough diagnostic, NOT a "
                "primary valuation, and do NOT lead the thesis with it; lean on forward-earnings and "
                "cycle-based lenses instead."
            )

    cons = dossier.consensus
    if cons is not None:
        lines.append("\nCONSENSUS / ANALYST EXPECTATIONS (yfinance estimate; may be unreliable):")
        for label, val in (
            ("forward_pe", cons.forward_pe), ("peg", cons.peg),
            ("target_mean", cons.target_mean), ("target_upside_pct", cons.target_upside_pct),
            ("rating", cons.rating), ("n_analysts", cons.n_analysts),
            ("last_eps_surprise_pct", cons.last_eps_surprise_pct),
        ):
            if val is not None:
                lines.append(f"- {label}: {val}")
        if cons.rejected:
            lines.append(f"- rejected (failed validation, withheld): {'; '.join(cons.rejected)}")

    if dossier.filing_sections:
        lines.append("\nFILING SECTIONS:")
        for s in dossier.filing_sections:
            # The earnings-release segment/highlights section carries the segment table,
            # adjusted FCF, and guidance — give it a larger budget than 10-K prose.
            budget = _CTX_EARNINGS_SECTION_CHARS if "Segment" in s.name else _CTX_SECTION_CHARS
            excerpt = (s.excerpt or "")[:budget]
            lines.append(f"- {s.name} (source: {s.provenance.source}): {excerpt}")

    if dossier.material_events:
        lines.append("\nMATERIAL EVENTS (SEC 8-K):")
        recent = sorted(dossier.material_events, key=lambda e: e.filing_date, reverse=True)
        for ev in recent[:_CTX_MAX_EVENTS]:
            label = ev.title or ", ".join(ev.item_codes) or "8-K"
            lines.append(f"- {ev.filing_date}: {label} (source: {ev.provenance.source})")
            if ev.excerpt:
                lines.append(f"  {ev.excerpt[:_CTX_EVENT_CHARS]}")

    if dossier.macro and dossier.macro.series:
        lines.append("\nMACRO:")
        for m in dossier.macro.series:
            latest = m.observations[-1] if m.observations else None
            val = f"{latest[1]} (as of {latest[0]})" if latest else "n/a"
            lines.append(f"- {m.title} [{m.series_id}]: {val} (source: {m.provenance.source})")

    if dossier.news:
        lines.append("\nNEWS:")
        for n in dossier.news:
            line = f"- {n.title}" + (f" — {n.publisher}" if n.publisher else "")
            if n.link:
                line += f" (source: {n.link})"
            lines.append(line)

    if dossier.gaps:
        lines.append("\nDATA GAPS (sources unavailable this run):")
        for g in dossier.gaps:
            lines.append(f"- {g.source}: {g.reason}")

    return "\n".join(lines)


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


class LLMResponseError(RuntimeError):
    """Raised when the LLM response can't be parsed into the expected schema."""


def _coerce_str(value: object) -> str:
    """Coerce an LLM field value to a plain string. Models sometimes return a
    section (e.g. open_questions) as a JSON array/object even when a string is
    asked for; join arrays with newlines and stringify objects rather than fail."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(_coerce_str(v) for v in value)
    if isinstance(value, dict):
        return "\n".join(f"{k}: {_coerce_str(v)}" for k, v in value.items())
    return str(value)


def _parse(model_cls: type[_T], raw: str, schema: str) -> _T:
    """Parse an LLM JSON response into `model_cls`, or raise LLMResponseError.

    Tolerates non-string field values (coerced to strings) and surfaces truncated
    or malformed JSON as a clean LLMResponseError instead of a stack trace."""
    try:
        data = json.loads(_extract_json(raw))
    except ValueError as exc:
        raise LLMResponseError(
            f"model returned malformed or truncated JSON for {schema}"
        ) from exc
    if isinstance(data, dict):
        data = {k: _coerce_str(v) for k, v in data.items()}
    try:
        return model_cls.model_validate(data)
    except ValidationError as exc:
        raise LLMResponseError(
            f"model returned JSON that does not match the {schema} schema"
        ) from exc


def analyze(
    company: CompanyDossier, llm: LLMClient, *, model: str | None = None
) -> AnalysisSections:
    prompt = (
        "OUTPUT_SCHEMA=analysis\n"
        f"Company data:\n{_company_context(company)}\n\n"
        "Return ONLY a JSON object with these string keys: "
        "executive_summary, company_overview, business_segments, "
        "financial_snapshot, valuation_discussion, key_risks, open_questions. "
        "Each value MUST be a single plain string (use newlines within a value "
        "for lists; do NOT return arrays or nested objects)."
    )
    logger.info("analyze: %s", company.ticker)
    raw = llm.complete(ANALYSIS_SYSTEM, prompt, model=model, max_tokens=_MAX_OUTPUT_TOKENS)
    return _parse(AnalysisSections, raw, "analysis")


def debate(
    company: CompanyDossier, llm: LLMClient, *, model: str | None = None
) -> DebateSections:
    prompt = (
        "OUTPUT_SCHEMA=debate\n"
        f"Company data:\n{_company_context(company)}\n\n"
        "Return ONLY a JSON object with these string keys: "
        "bull_thesis, bear_thesis, final_view. "
        "Each value MUST be a single plain string (not an array or nested object)."
    )
    logger.info("debate: %s", company.ticker)
    raw = llm.complete(DEBATE_SYSTEM, prompt, model=model, max_tokens=_MAX_OUTPUT_TOKENS)
    return _parse(DebateSections, raw, "debate")


def _build_sources(dossier: CompanyDossier, *, mock: bool) -> list[str]:
    if mock:
        return ["MOCK fixture data — not real market sources"]
    sources: list[str] = []
    seen: set[str] = set()

    def _add(label: str) -> None:
        if label and label not in seen:
            seen.add(label)
            sources.append(label)

    if dossier.quote:
        _add(dossier.quote.provenance.source)
    if dossier.fundamentals:
        for f in dossier.fundamentals.facts:
            url = f.provenance.source_url
            _add(url or f.provenance.source)
    for s in dossier.filing_sections:
        _add(s.provenance.source_url or s.provenance.source)
    if dossier.macro:
        for m in dossier.macro.series:
            _add(m.provenance.source)
    for n in dossier.news:
        if n.link:
            _add(n.link)
    for g in dossier.gaps:
        _add(f"(gap) {g.source}: {g.reason}")
    return sources


def run(
    company: CompanyDossier,
    llm: LLMClient,
    *,
    model_used: str,
    mock: bool,
) -> ResearchReport:
    """Run the full pipeline and return an assembled ResearchReport."""
    call_model = None if mock else model_used
    analysis = analyze(company, llm, model=call_model)
    deb = debate(company, llm, model=call_model)
    review = critique(analysis, deb, company, llm, model=call_model)
    return ResearchReport(
        ticker=company.ticker,
        company=company,
        analysis=analysis,
        debate=deb,
        generated_at=date.today(),
        model_used=model_used,
        mock=mock,
        sources=_build_sources(company, mock=mock),
        critic_review=review,
    )
