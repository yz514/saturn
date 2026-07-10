"""The Critic: advisory verification of a drafted report against the dossier."""
from __future__ import annotations

import json
import logging
import re

from saturn.models import CompanyDossier, CriticFinding, CriticReview, Provenance

logger = logging.getLogger(__name__)
_MAX_OUTPUT_TOKENS = 8192

# A genuine dollar token is either $-prefixed (optionally with a magnitude word) OR a
# number with a magnitude suffix. This avoids grabbing a bare year like "2025" from
# "FY2025" as if it were a value.
_DOLLAR_TOKEN_RE = re.compile(
    r"\$\s*([\d,]+(?:\.\d+)?)\s*(trillion|billion|million|bn|mn|[bmt])?\b"
    r"|\b([\d,]+(?:\.\d+)?)\s*(trillion|billion|million|bn|mn|[bmt])\b",
    re.IGNORECASE,
)
_MULT = {"t": 1e12, "trillion": 1e12, "b": 1e9, "bn": 1e9, "billion": 1e9, "m": 1e6, "mn": 1e6, "million": 1e6}


def _dollar_tokens(text: str) -> list[tuple[float, str]]:
    """(value, raw-digit-string) for every genuine dollar token in `text`."""
    out: list[tuple[float, str]] = []
    for m in _DOLLAR_TOKEN_RE.finditer(text or ""):
        num = m.group(1) or m.group(3)
        suffix = (m.group(2) or m.group(4) or "").lower()
        try:
            digits = num.replace(",", "")
            out.append((float(digits) * _MULT.get(suffix, 1.0), digits))
        except (TypeError, ValueError):
            continue
    return out


def _dossier_values(dossier: CompanyDossier) -> list[float]:
    vals = [m.value for m in dossier.derived_metrics if m.value is not None]
    if dossier.fundamentals:
        vals += [f.value for f in dossier.fundamentals.facts if f.value is not None]
    if dossier.quote and dossier.quote.market_cap:
        vals.append(dossier.quote.market_cap)
    return vals


def is_dollar_grounded(token: str, dossier: CompanyDossier, *, tol: float = 0.02) -> bool:
    """True if ANY genuine dollar token in `token` matches a dossier fact/metric within
    `tol`, or its digits appear in the ingested filing/press-release source text."""
    toks = _dollar_tokens(token)
    if not toks:
        return False
    dvals = _dossier_values(dossier)
    source = " ".join((s.excerpt or "") for s in dossier.filing_sections).replace(",", "")
    for v, digits in toks:
        if v == 0:
            continue
        if any(dv and abs(v - dv) <= tol * abs(dv) for dv in dvals):
            return True
        if digits and digits in source:
            return True
    return False


CRITIC_SYSTEM = (
    "You are a skeptical verification analyst. You are given a DRAFT equity research "
    "report and the UNDERLYING provenance-tagged data the analyst was given. Your ONLY "
    "job is to find where the report's claims are NOT supported by that data. Check: "
    "(1) quantitative factual claims not traceable to a provided datum/source "
    "(category unsupported_number); (2) internal contradictions — a statement conflicting "
    "with another statement or a table in the report (category contradiction); (3) whether "
    "the thesis leads with a signal flagged LOW CONFIDENCE (category over_weighting). "
    "Quote claims exactly but keep each 'claim' a SHORT quote (under 20 words). "
    "CRITICAL: only report ACTUAL problems. If a claim is supported/correct/consistent with "
    "the data, OMIT it entirely — never list a claim just to confirm it. Do NOT invent "
    "issues. Respond with ONLY a single valid JSON "
    "object — no prose, no code fences — and escape all quotes and newlines inside string "
    "values so the JSON parses."
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


# The LLM sometimes lists claims it then affirms as SUPPORTED (against instructions);
# drop those — a finding must be a genuine problem, not a confirmation.
_SUPPORTED_RE = re.compile(
    r"claim (is )?supported|is supported\b|supported\.|is correct\b|claim is correct|"
    r"broadly consistent|matches (the )?data|consistent with the data",
    re.IGNORECASE,
)


def _is_non_issue(f, dossier: CompanyDossier) -> bool:
    """True when a finding isn't a real problem: its evidence affirms support, or it's an
    unsupported_number whose dollar figure is actually grounded in the data."""
    if _SUPPORTED_RE.search(f.evidence or ""):
        return True
    return f.category == "unsupported_number" and is_dollar_grounded(f.claim, dossier)


def _safe_int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _build_review(data: dict, dossier: CompanyDossier) -> CriticReview:
    """Build a CriticReview from parsed JSON, validating each finding INDIVIDUALLY so one
    malformed finding is skipped rather than discarding the whole review. Then apply the
    deterministic backstop (drop unsupported_number findings whose $ figure is grounded)."""
    findings: list[CriticFinding] = []
    for raw_f in (data.get("findings") or []):
        try:
            findings.append(CriticFinding.model_validate(raw_f))
        except Exception:  # noqa: BLE001 - skip a single malformed finding
            continue
    findings = [f for f in findings if not _is_non_issue(f, dossier)]
    return CriticReview(
        findings=findings,
        claims_checked=_safe_int(data.get("claims_checked")),
        summary=str(data.get("summary") or ""),
        provenance=Provenance(source="Saturn (critic)"),
    )


def critique(analysis, debate, dossier: CompanyDossier, llm, *, model: str | None = None) -> CriticReview | None:
    """Advisory verification. Resilient to imperfect LLM JSON: retries once on a parse
    failure, then validates findings individually. Returns None (soft-fail) only when both
    attempts fail to yield parseable JSON — never breaks the report."""
    from saturn.analytics.forward import is_reverse_dcf_low_confidence
    from saturn.workflows.equity_research import _company_context, _extract_json
    try:
        fwd = [m for m in dossier.derived_metrics if m.provenance.source == "Saturn (model)"]
        prompt = _critic_prompt(analysis, debate, _company_context(dossier), is_reverse_dcf_low_confidence(fwd))
        strict = "\n\nIMPORTANT: your previous reply was not valid JSON. Return ONLY a single, strictly valid JSON object; escape every quote and newline inside string values."
        for attempt in range(2):
            raw = llm.complete(CRITIC_SYSTEM, prompt if attempt == 0 else prompt + strict,
                               model=model, max_tokens=_MAX_OUTPUT_TOKENS)
            try:
                return _build_review(json.loads(_extract_json(raw)), dossier)
            except Exception:  # noqa: BLE001 - malformed JSON; retry once then give up
                continue
        logger.warning("critic unavailable for %s: JSON unparseable after retry", getattr(dossier, "ticker", "?"))
        return None
    except Exception as exc:  # noqa: BLE001 - critic is advisory, never breaks the report
        logger.warning("critic unavailable for %s: %s", getattr(dossier, "ticker", "?"), exc)
        return None
