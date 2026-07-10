"""The Critic: advisory verification of a drafted report against the dossier."""
from __future__ import annotations

import json
import logging
import re

from saturn.models import CompanyDossier, CriticReview

logger = logging.getLogger(__name__)
_MAX_OUTPUT_TOKENS = 8192

_DOLLAR_RE = re.compile(r"\$?\s*([\d,]+(?:\.\d+)?)\s*(trillion|billion|million|bn|mn|[bmt])?\b", re.IGNORECASE)
_MULT = {"t": 1e12, "trillion": 1e12, "b": 1e9, "bn": 1e9, "billion": 1e9, "m": 1e6, "mn": 1e6, "million": 1e6}


def _parse_dollar(token: str) -> float | None:
    m = _DOLLAR_RE.search(token or "")
    if not m:
        return None
    try:
        num = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    return num * _MULT.get((m.group(2) or "").lower(), 1.0)


def _dossier_values(dossier: CompanyDossier) -> list[float]:
    vals = [m.value for m in dossier.derived_metrics if m.value is not None]
    if dossier.fundamentals:
        vals += [f.value for f in dossier.fundamentals.facts if f.value is not None]
    if dossier.quote and dossier.quote.market_cap:
        vals.append(dossier.quote.market_cap)
    return vals


def is_dollar_grounded(token: str, dossier: CompanyDossier, *, tol: float = 0.02) -> bool:
    """True if a $-magnitude token matches a dossier fact/metric within `tol`, or its
    digits appear in the ingested filing/press-release source text."""
    v = _parse_dollar(token)
    if v is None or v == 0:
        return False
    for dv in _dossier_values(dossier):
        if dv and abs(v - dv) <= tol * abs(dv):
            return True
    digits = re.sub(r"[^\d.]", "", token)
    source = " ".join((s.excerpt or "") for s in dossier.filing_sections).replace(",", "")
    return bool(digits) and digits in source


CRITIC_SYSTEM = (
    "You are a skeptical verification analyst. You are given a DRAFT equity research "
    "report and the UNDERLYING provenance-tagged data the analyst was given. Your ONLY "
    "job is to find where the report's claims are NOT supported by that data. Check: "
    "(1) quantitative factual claims not traceable to a provided datum/source "
    "(category unsupported_number); (2) internal contradictions — a statement conflicting "
    "with another statement or a table in the report (category contradiction); (3) whether "
    "the thesis leads with a signal flagged LOW CONFIDENCE (category over_weighting). "
    "Quote claims exactly. Do NOT invent issues; if a claim checks out, omit it. "
    "Respond with ONLY a valid JSON object, no prose, no code fences."
)


def _critic_prompt(analysis, debate, context: str, low_conf: bool) -> str:
    sections = {**analysis.model_dump(), **debate.model_dump()}
    report_text = "\n\n".join(f"[{k}]\n{v}" for k, v in sections.items())
    note = ("\nNOTE: the reverse-DCF is flagged LOW CONFIDENCE; if the thesis leads with its "
            "fair value or margin of safety, report it as category over_weighting.\n" if low_conf else "")
    return (
        "OUTPUT_SCHEMA=critic\n"
        "DRAFT REPORT (verify this prose):\n" + report_text + "\n\n"
        "UNDERLYING DATA (provenance-tagged):\n" + context + "\n" + note +
        "\nReturn ONLY: {\"claims_checked\": int, \"summary\": str, \"findings\": "
        "[{\"claim\": str, \"section\": str, \"category\": str, \"verdict\": str, "
        "\"evidence\": str, \"severity\": str}]}. category in "
        "[unsupported_number, contradiction, over_weighting, unverified_claim]."
    )


def critique(analysis, debate, dossier: CompanyDossier, llm, *, model: str | None = None) -> CriticReview | None:
    """Advisory verification. Returns None (soft-fail) on any LLM/parse error."""
    from saturn.analytics.forward import is_reverse_dcf_low_confidence
    from saturn.workflows.equity_research import _company_context, _extract_json
    try:
        fwd = [m for m in dossier.derived_metrics if m.provenance.source == "Saturn (model)"]
        prompt = _critic_prompt(analysis, debate, _company_context(dossier), is_reverse_dcf_low_confidence(fwd))
        raw = llm.complete(CRITIC_SYSTEM, prompt, model=model, max_tokens=_MAX_OUTPUT_TOKENS)
        data = json.loads(_extract_json(raw))
        data["provenance"] = {"source": "Saturn (critic)"}
        review = CriticReview.model_validate(data)
    except Exception as exc:  # noqa: BLE001 - critic is advisory, never breaks the report
        logger.warning("critic unavailable for %s: %s", getattr(dossier, "ticker", "?"), exc)
        return None
    # deterministic backstop: drop unsupported_number findings whose $ figure IS grounded
    review.findings = [
        f for f in review.findings
        if not (f.category == "unsupported_number" and is_dollar_grounded(f.claim, dossier))
    ]
    return review
