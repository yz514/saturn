"""The Critic: advisory verification of a drafted report against the dossier."""
from __future__ import annotations

import json
import logging
import re

from saturn.models import CompanyDossier, CriticFinding, CriticReview, Provenance

logger = logging.getLogger(__name__)
_MAX_OUTPUT_TOKENS = 8192

# The Critic's claims carry numbers in several units: $-magnitudes, percentages, and
# ratios ("x"). Only UNIT-bearing numbers are grounded — a bare year like "2025" (from
# "FY2025") or a plain count is deliberately skipped as too ambiguous to match.
_MULT = {"t": 1e12, "trillion": 1e12, "b": 1e9, "bn": 1e9, "billion": 1e9, "m": 1e6, "mn": 1e6, "million": 1e6}
_NUM_TOKEN_RE = re.compile(
    r"(-?\$?\s*[\d,]+(?:\.\d+)?)\s*(trillion|billion|million|bn|mn|[bmt](?![a-z]))?\s*"
    r"(%|percentage points?|ppt|pp|bps|x)?",
    re.IGNORECASE,
)


def _meaningful_numbers(text: str) -> list[tuple[list[float], str]]:
    """For each unit-bearing number ($, magnitude word, % or x), its plausible float
    interpretations + raw digit string. Unit-less bare numbers are skipped."""
    out: list[tuple[list[float], str]] = []
    for m in _NUM_TOKEN_RE.finditer(text or ""):
        raw, suffix, unit = m.group(1) or "", (m.group(2) or "").lower(), (m.group(3) or "").lower()
        digits = raw.replace("$", "").replace(",", "").replace(" ", "")
        try:
            base = float(digits)
        except ValueError:
            continue
        # Skip a bare, unit-less year-like integer (e.g. "2025" from "FY2025"); other
        # bare numbers (VIX 15.8, DXY 120.7) are kept so macro claims can be grounded.
        if not ("$" in raw or suffix or unit) and "." not in digits and 1900 <= abs(base) <= 2099:
            continue
        if suffix:
            cands = [base * _MULT.get(suffix, 1.0)]
        elif unit in ("%", "pp", "ppt") or unit.startswith("percentage point"):
            cands = [base / 100.0, base]   # fraction or percent; "N percentage points" -> N/100
        elif unit == "bps":
            cands = [base / 10000.0, base]
        else:                              # "x" ratio, or a plain $-value
            cands = [base]
        out.append((cands, digits.lstrip("-")))
    return out


def _dossier_values(dossier: CompanyDossier) -> list[float]:
    vals = [m.value for m in dossier.derived_metrics if m.value is not None]
    if dossier.fundamentals:
        vals += [f.value for f in dossier.fundamentals.facts if f.value is not None]
    if dossier.quote and dossier.quote.market_cap:
        vals.append(dossier.quote.market_cap)
    if dossier.macro:
        for s in dossier.macro.series:
            if s.observations:
                try:
                    vals.append(float(s.observations[-1][1]))
                except (TypeError, ValueError, IndexError):
                    continue
    return vals


def _grounded_in_source(digits: str, source: str) -> bool:
    """True only when `digits` is a specific figure (>= 3 significant digits) that appears
    as a standalone number in `source`. Guards against spurious substring matches: a bare
    '2' or '12' trivially occurs in any long filing (dates, notes, other figures), and a
    3-digit token like '18.3' must not match inside a larger number such as '118.35'."""
    if sum(c.isdigit() for c in digits) < 3:
        return False
    return re.search(r"(?<!\d)" + re.escape(digits) + r"(?!\d)", source) is not None


def is_number_grounded(claim: str, dossier: CompanyDossier, *, tol: float = 0.02) -> bool:
    """True if ANY unit-bearing number in `claim` ($, %, x, magnitude) matches a dossier
    fact/metric within `tol`, or is a specific (>=3 sig-digit) figure quoted verbatim in the
    ingested source text. Used to drop unsupported_number findings that are actually grounded."""
    toks = _meaningful_numbers(claim)
    if not toks:
        return False
    dvals = _dossier_values(dossier)
    source = " ".join((s.excerpt or "") for s in dossier.filing_sections).replace(",", "")
    for cands, digits in toks:
        for v in cands:
            if v != 0 and any(dv and abs(v - dv) <= tol * abs(dv) for dv in dvals):
                return True
        if digits and _grounded_in_source(digits, source):
            return True
    return False


# Backward-compatible alias (percentages/ratios are handled too now, not just dollars).
is_dollar_grounded = is_number_grounded


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


def _alpha_text(alpha) -> str:
    """Flatten an AlphaThesis into text the Critic can scan. Omits computed fields
    (implied_price, implied_return_pct) and self-assessed confidence — the Critic audits
    the thesis inputs, not model-derived outputs."""
    legs = "; ".join(
        f"{s.name} {s.period}: {s.per_share_value:g} {s.metric} x {s.multiple:g} {s.multiple_basis} "
        f"(driver: {s.driver})" for s in alpha.scenarios) or "none"
    return (f"stance={alpha.stance} vs anchor [{alpha.anchor.source}: {alpha.anchor.text}]; "
            f"variant: {alpha.variant}; rationale: {alpha.rationale}; "
            f"key_variable: {alpha.key_variable}; falsifier: {alpha.falsifier}; "
            f"horizon: {alpha.horizon}; scenarios: {legs}")


def _critic_prompt(analysis, debate, context: str, low_conf: bool, alpha=None) -> str:
    sections = {**analysis.model_dump(), **debate.model_dump()}
    if alpha is not None:
        sections["alpha_thesis"] = _alpha_text(alpha)
    report_text = "\n\n".join(f"[{k}]\n{v}" for k, v in sections.items())
    note = ("\nNOTE: the reverse-DCF is flagged LOW CONFIDENCE. Report over_weighting ONLY if "
            "the thesis RELIES on its fair value / margin of safety as a PRIMARY argument. If the "
            "report explicitly dismisses or caveats it (e.g. 'diagnostic only', 'not a primary "
            "lens'), that is CORRECT handling — do NOT flag it.\n" if low_conf else "")
    alpha_note = ("\nThe report includes an ALPHA THESIS. Also flag category "
                  "unsupported_alpha_inference when: the variant is not connected to the anchor; a "
                  "scenario driver has no support in the data; the falsifier is not an observable "
                  "event with a time window; or a conclusion is stronger than its evidence (e.g. an "
                  "accounting inference with no contract-liability / deferred-revenue / filing "
                  "support). Also flag it when the alpha STANCE contradicts the Final View — e.g. "
                  "stance below_consensus while the Final View reads as an aggressive buy.\n"
                  if alpha is not None else "")
    categories = ("[unsupported_number, contradiction, over_weighting, unverified_claim"
                  + (", unsupported_alpha_inference]" if alpha is not None else "]"))
    return (
        "OUTPUT_SCHEMA=critic\n"
        "DRAFT REPORT (verify this prose):\n" + report_text + "\n\n"
        "UNDERLYING DATA (provenance-tagged):\n" + context + "\n" + note + alpha_note +
        "\nReturn ONLY: {\"claims_checked\": int, \"summary\": str, \"findings\": "
        "[{\"claim\": str, \"section\": str, \"category\": str, \"verdict\": str, "
        "\"evidence\": str, \"severity\": str}]}. category in " + categories + "."
    )


# The LLM often lists claims it then AFFIRMS as supported (against instructions). Drop
# those — a finding must be a genuine problem. Deliberately conservative (only strong
# standalone affirmations unlikely to be negated) so real findings aren't dropped; the
# numeric-grounding backstop handles most numeric noise regardless of wording.
_SUPPORTED_RE = re.compile(
    r"\bconfirmed\b|\bcompliant\b|\bno (material )?(issue|error|problem|discrepanc)|"
    r"\bcalculations? confirmed\b|claim (is )?supported|\bmatches (the )?data\b|"
    r"acceptable rounding|rounding is acceptable|\bno material error\b",
    re.IGNORECASE,
)


def _is_non_issue(f, dossier: CompanyDossier) -> bool:
    """True when a finding isn't a real problem: its evidence affirms support, or it's an
    unsupported_number whose numbers ($/%/x) are actually grounded in the data."""
    if _SUPPORTED_RE.search(f.evidence or ""):
        return True
    return f.category == "unsupported_number" and is_number_grounded(f.claim, dossier)


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


_SEVERITY_WEIGHT = {"high": 3, "medium": 2, "low": 1}
_ACTIONABLE_CATEGORIES = {"contradiction", "unsupported_number", "over_weighting"}


def _is_actionable_finding(f) -> bool:
    """A finding worth a repair pass: a high/medium error the analyst can act on
    (a wrong figure, an internal contradiction, or leading with a low-confidence signal).
    Low-severity notes and generic unverified_claim stay advisory."""
    return f.severity in ("high", "medium") and f.category in _ACTIONABLE_CATEGORIES


def _actionable(review: CriticReview) -> bool:
    return any(_is_actionable_finding(f) for f in review.findings)


def _is_alpha_actionable(f) -> bool:
    """A high/medium finding on the alpha thesis — repairable by rewriting its prose fields.
    (Kept separate from _is_actionable_finding: alpha findings do not trigger section revise.)"""
    return f.severity in ("high", "medium") and (f.section or "").startswith("alpha_thesis")


def _alpha_actionable(review: CriticReview) -> bool:
    return any(_is_alpha_actionable(f) for f in review.findings)


def _score(review: CriticReview) -> int:
    """Severity-weighted issue score (high=3, medium=2, low=1); lower is better."""
    return sum(_SEVERITY_WEIGHT.get(f.severity, 1) for f in review.findings)


REVISE_SYSTEM = (
    "You are correcting a draft equity research report. You are given specific VERIFIED "
    "problems (each with the underlying data as evidence) and the current text of the "
    "affected sections. Rewrite ONLY those sections to fix exactly these problems using the "
    "cited evidence — correct the wrong figure/claim, or stop leading with an over-weighted "
    "low-confidence signal. Preserve everything else in each section verbatim; add no new "
    "claims. Respond with ONLY a JSON object mapping each affected section name to its "
    "corrected full text (plain strings), no prose, no code fences."
)


def revise(analysis, debate, review: CriticReview, dossier: CompanyDossier, llm, *,
           model: str | None = None) -> dict | None:
    """Return {section: corrected_text} for the actionable-finding sections, or None
    (soft-fail). Only the affected sections are rewritten; unaffected sections are never
    returned so the caller can splice deterministically."""
    from saturn.workflows.equity_research import _company_context, _extract_json
    try:
        actionable = [f for f in review.findings if _is_actionable_finding(f)]
        sections = {**analysis.model_dump(), **debate.model_dump()}
        if any(f.category == "contradiction" for f in actionable):
            affected = sorted(sections.keys())
        else:
            affected = sorted({f.section for f in actionable if f.section in sections})
        if not affected:
            return None
        problems = "\n".join(
            f'- [{f.section}] ({f.category}, {f.severity}): "{f.claim}" -- {f.evidence}'
            for f in actionable if f.section in affected
        )
        current = {s: sections[s] for s in affected}
        prompt = (
            "OUTPUT_SCHEMA=revise\n"
            "VERIFIED PROBLEMS to fix:\n" + problems + "\n\n"
            "CURRENT SECTION TEXT (JSON):\n" + json.dumps(current) + "\n\n"
            "UNDERLYING DATA (provenance-tagged):\n" + _company_context(dossier) + "\n\n"
            f"Return ONLY a JSON object mapping each of {affected} to its corrected full text."
        )
        raw = llm.complete(REVISE_SYSTEM, prompt, model=model, max_tokens=_MAX_OUTPUT_TOKENS)
        data = json.loads(_extract_json(raw))
        out = {k: str(v) for k, v in data.items() if k in affected and isinstance(v, str)}
        return out or None
    except Exception as exc:  # noqa: BLE001 - revise is best-effort; keep the original report
        logger.warning("critic revise unavailable for %s: %s", getattr(dossier, "ticker", "?"), exc)
        return None


REVISE_ALPHA_SYSTEM = (
    "You are correcting the ALPHA THESIS of an equity research report. You are given specific "
    "VERIFIED problems on the thesis (each with the underlying data as evidence) and the current "
    "text of its prose fields. Rewrite ONLY those prose fields to fix exactly these problems using "
    "the cited data. Do NOT change the stance, do NOT change the scenario numbers or assumptions, "
    "and do NOT invent figures — preserve everything else in each field. Respond with ONLY a JSON "
    "object mapping each affected prose-field name to its corrected full text (plain strings), no "
    "prose, no code fences."
)


def revise_alpha(alpha, dossier: CompanyDossier, findings, llm, *, model: str | None = None) -> dict | None:
    """Return {prose_field: corrected_text} for the alpha thesis, or None (soft-fail). Only the
    ALPHA_PROSE_FIELDS are rewritten; any stance/scenarios/anchor key the model returns is dropped."""
    from saturn.workflows.equity_research import _company_context, _extract_json
    from saturn.models import ALPHA_PROSE_FIELDS
    try:
        problems = "\n".join(
            f'- ({f.category}, {f.severity}): "{f.claim}" -- {f.evidence}' for f in findings
        )
        current = {k: getattr(alpha, k) for k in ALPHA_PROSE_FIELDS}
        prompt = (
            "OUTPUT_SCHEMA=revise_alpha\n"
            "VERIFIED PROBLEMS to fix:\n" + problems + "\n\n"
            "CURRENT ALPHA PROSE (JSON):\n" + json.dumps(current) + "\n\n"
            "UNDERLYING DATA (provenance-tagged):\n" + _company_context(dossier) + "\n\n"
            f"Return ONLY a JSON object mapping affected fields (subset of {list(ALPHA_PROSE_FIELDS)}) "
            "to corrected full text. Do NOT include stance, scenarios, or anchor."
        )
        raw = llm.complete(REVISE_ALPHA_SYSTEM, prompt, model=model, max_tokens=_MAX_OUTPUT_TOKENS)
        data = json.loads(_extract_json(raw))
        out = {k: str(v) for k, v in data.items() if k in ALPHA_PROSE_FIELDS and isinstance(v, str)}
        return out or None
    except Exception as exc:  # noqa: BLE001 - best-effort; keep the original alpha thesis
        logger.warning("critic revise_alpha unavailable for %s: %s", getattr(dossier, "ticker", "?"), exc)
        return None


def critique(analysis, debate, dossier: CompanyDossier, llm, *, model: str | None = None, alpha=None) -> CriticReview | None:
    """Advisory verification. Resilient to imperfect LLM JSON: retries once on a parse
    failure, then validates findings individually. Returns None (soft-fail) only when both
    attempts fail to yield parseable JSON — never breaks the report."""
    from saturn.analytics.forward import is_reverse_dcf_low_confidence
    from saturn.workflows.equity_research import _company_context, _extract_json
    try:
        fwd = [m for m in dossier.derived_metrics if m.provenance.source == "Saturn (model)"]
        prompt = _critic_prompt(analysis, debate, _company_context(dossier),
                                is_reverse_dcf_low_confidence(fwd), alpha)
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
