"""The Synthesist: turns analysis + debate into a structured, auditable Alpha Thesis."""
from __future__ import annotations

import json
import logging

from saturn.models import AlphaThesis, CompanyDossier, ExpectationAnchor, Provenance, ScenarioLeg

logger = logging.getLogger(__name__)
_MAX_OUTPUT_TOKENS = 8192
_STANCE_BAND = 0.10  # ±10 percentage points around the consensus target defines "in line"


def _derive_stance(base_return: float | None, target_upside: float | None) -> str | None:
    """Consensus-relative stance from Saturn's base-case return vs the Street's target upside.
    Returns None when it can't be derived (no target / no base return) so the caller keeps the
    LLM-declared stance. Because it reads the base leg, it can never contradict the scenarios."""
    if base_return is None or target_upside is None:
        return None
    if base_return >= target_upside + _STANCE_BAND:
        return "above_consensus"
    if base_return <= target_upside - _STANCE_BAND:
        return "below_consensus"
    return "in_line_consensus"


def _resolve_anchor(dossier: CompanyDossier) -> ExpectationAnchor:
    """Deterministic market-expectation anchor: consensus first, else reverse-DCF implied, else none."""
    from saturn.analytics.forward import is_reverse_dcf_low_confidence

    cons = dossier.consensus
    if cons is not None and any(v is not None for v in (cons.forward_pe, cons.forward_eps, cons.target_mean)):
        if cons.forward_pe is not None:
            metric, value, unit = "Forward P/E", cons.forward_pe, "x"
        elif cons.forward_eps is not None:
            metric, value, unit = "forward EPS", cons.forward_eps, "USD/share"
        else:
            metric, value, unit = "mean price target", cons.target_mean, "USD/share"
        parts: list[str] = []
        if cons.forward_pe is not None:
            parts.append(f"forward P/E {cons.forward_pe:.1f}x")
        if cons.forward_eps is not None and cons.forward_pe is None:
            parts.append(f"forward EPS ${cons.forward_eps:.2f}/share")
        if cons.target_mean is not None:
            up = f" ({cons.target_upside_pct:+.0%} vs price)" if cons.target_upside_pct is not None else ""
            parts.append(f"mean target ${cons.target_mean:,.0f}{up}")
        if cons.rating:
            parts.append(f"rating {cons.rating}")
        if cons.n_analysts:
            parts.append(f"{cons.n_analysts} analysts")
        text = "Consensus: " + ", ".join(parts) + "." if parts else "Consensus estimates available."
        return ExpectationAnchor(source="consensus", metric=metric, period="NTM", value=value,
                                 unit=unit, text=text, confidence="low" if cons.rejected else "medium")

    fwd = [m for m in dossier.derived_metrics if m.provenance.source == "Saturn (model)"]
    implied = next((m for m in fwd if m.name == "implied_fcf_growth"), None)
    if implied is not None:
        low = is_reverse_dcf_low_confidence(fwd)
        note = " (LOW CONFIDENCE — trailing FCF base likely cycle-depressed)" if low else ""
        return ExpectationAnchor(source="reverse_dcf_implied", metric="implied FCF growth",
                                 period="perpetual", value=implied.value, unit="fraction",
                                 text=f"Price implies ~{implied.value:.0%} FCF growth{note}.",
                                 confidence="low" if low else "medium")

    return ExpectationAnchor(source="none", text="No market-expectation anchor available this run.",
                             confidence="low")


def _price_scenarios(legs: list[ScenarioLeg], quote_price: float | None) -> list[ScenarioLeg]:
    """Compute implied_price = per_share_value × multiple and return vs the current quote."""
    out: list[ScenarioLeg] = []
    for leg in legs:
        price = leg.per_share_value * leg.multiple
        ret = (price / quote_price - 1) if (quote_price and quote_price > 0) else None
        out.append(leg.model_copy(update={"implied_price": price, "implied_return_pct": ret}))
    return out


def alpha_completeness(thesis: AlphaThesis) -> list[str]:
    """Structural-presence gaps only (semantic quality is the Critic's job). Returns
    human-readable gap strings; empty list means structurally complete."""
    gaps: list[str] = []
    if thesis.anchor.source == "none":
        gaps.append("no market-expectation anchor")
    if thesis.stance != "unclear" and not thesis.rationale.strip():
        gaps.append("stance without rationale")
    if not thesis.variant.strip():
        gaps.append("missing variant")
    elif len(thesis.variant.split()) > 50:
        gaps.append("variant too long (>50 words)")
    if not thesis.key_variable.strip():
        gaps.append("missing key variable")
    if not thesis.falsifier.strip():
        gaps.append("missing falsifier")
    if not thesis.horizon.strip():
        gaps.append("missing horizon")
    if len(thesis.scenarios) < 3:
        gaps.append("fewer than 3 scenarios")
    for s in thesis.scenarios:
        if not s.period.strip():
            gaps.append(f"scenario '{s.name}' missing period")
    return gaps


SYNTHESIZE_SYSTEM = (
    "You are a portfolio manager turning an analyst's memo into a tradeable view. You are given "
    "the market-expectation ANCHOR, the draft report, and the underlying data. State whether the "
    "view is above / in line with / below the anchor and WHY, grounded in specific data. If you "
    "cannot honestly take a differentiated view, return stance 'unclear' — never manufacture one. "
    "Give the single key variable that decides it, an OBSERVABLE falsifier (a concrete event plus a "
    "time window), a horizon, and exactly three scenarios (bull/base/bear). Each scenario states a "
    "period, a per-share metric with its value and basis, and a multiple with its basis — do NOT "
    "output prices; the system computes price = value x multiple. Keep 'variant' to ONE sentence "
    "under 35 words. Respond with ONLY a single valid JSON object, no prose, no code fences."
)


def _synthesize_prompt(analysis, debate, anchor: ExpectationAnchor, context: str) -> str:
    sections = {**analysis.model_dump(), **debate.model_dump()}
    report_text = "\n\n".join(f"[{k}]\n{v}" for k, v in sections.items())
    return (
        "OUTPUT_SCHEMA=alpha\n"
        f"MARKET-EXPECTATION ANCHOR ({anchor.source}): {anchor.text}\n\n"
        "DRAFT REPORT:\n" + report_text + "\n\n"
        "UNDERLYING DATA (provenance-tagged):\n" + context + "\n\n"
        "Return ONLY: {\"stance\": str, \"variant\": str, \"rationale\": str, \"confidence\": str, "
        "\"key_variable\": str, \"falsifier\": str, \"horizon\": str, \"scenarios\": "
        "[{\"name\": str, \"period\": str, \"driver\": str, \"metric\": str, \"metric_basis\": str, "
        "\"per_share_value\": number, \"multiple\": number, \"multiple_basis\": str}]}. "
        "stance in [above_consensus, in_line_consensus, below_consensus, unclear]. Your declared "
        "value is used ONLY when there is no consensus price target; otherwise the system derives "
        "stance deterministically from the base-case return vs consensus. "
        "confidence in [high, medium, low]. name in [bull, base, bear] (exactly 3). "
        "metric in [EPS, FCF/share, sales/share]; metric_basis in [GAAP, non_GAAP, adjusted, cycle_normalized]; "
        "multiple_basis in [P/E, P/FCF, P/S]. Do NOT output prices."
    )


def _one_of(value, allowed: tuple[str, ...], default: str) -> str:
    return value if value in allowed else default


def _build_thesis(data: dict, anchor: ExpectationAnchor, dossier: CompanyDossier) -> AlphaThesis:
    """Construct an AlphaThesis from parsed LLM data: validate scenarios per-leg (drop a
    single bad leg, keep the rest), price them, sanitize enum fields, and run the gate."""
    legs: list[ScenarioLeg] = []
    for raw in (data.get("scenarios") or []):
        try:
            legs.append(ScenarioLeg.model_validate(raw))
        except Exception:  # noqa: BLE001 - drop a single malformed leg, keep the rest
            continue
    quote_price = dossier.quote.price if dossier.quote else None
    legs = _price_scenarios(legs, quote_price)
    stance = _one_of(
        data.get("stance"),
        ("above_consensus", "in_line_consensus", "below_consensus", "unclear"),
        "unclear",
    )
    base_leg = next((s for s in legs if s.name == "base"), None)
    base_return = base_leg.implied_return_pct if base_leg else None
    target = dossier.consensus.target_upside_pct if dossier.consensus else None
    derived = _derive_stance(base_return, target)
    if derived is not None:
        stance = derived
        stance_basis = f"base {base_return:+.0%} vs consensus target {target:+.0%}"
    else:
        stance_basis = "vs model-implied anchor; no consensus target"
    thesis = AlphaThesis(
        anchor=anchor,
        stance=stance,
        stance_basis=stance_basis,
        variant=str(data.get("variant") or ""),
        rationale=str(data.get("rationale") or ""),
        confidence=_one_of(data.get("confidence"), ("high", "medium", "low"), "low"),
        key_variable=str(data.get("key_variable") or ""),
        falsifier=str(data.get("falsifier") or ""),
        horizon=str(data.get("horizon") or ""),
        scenarios=legs,
        provenance=Provenance(source="Saturn (synthesist)"),
    )
    thesis.incompleteness = alpha_completeness(thesis)
    return thesis


def synthesize(analysis, debate, dossier: CompanyDossier, llm, *, model: str | None = None) -> AlphaThesis | None:
    """Produce a structured AlphaThesis. Resilient to imperfect LLM JSON (one retry, per-leg
    validation, sanitized enums). Soft-fails to None; never breaks the report."""
    from saturn.workflows.equity_research import _company_context, _extract_json
    try:
        anchor = _resolve_anchor(dossier)
        prompt = _synthesize_prompt(analysis, debate, anchor, _company_context(dossier))
        strict = "\n\nIMPORTANT: your previous reply was not valid JSON. Return ONLY a single, strictly valid JSON object."
        for attempt in range(2):
            raw = llm.complete(SYNTHESIZE_SYSTEM, prompt if attempt == 0 else prompt + strict,
                               model=model, max_tokens=_MAX_OUTPUT_TOKENS)
            try:
                return _build_thesis(json.loads(_extract_json(raw)), anchor, dossier)
            except Exception:  # noqa: BLE001 - malformed JSON; retry once then give up
                continue
        logger.warning("synthesist unavailable for %s: JSON unparseable after retry", getattr(dossier, "ticker", "?"))
        return None
    except Exception as exc:  # noqa: BLE001 - synthesist is best-effort, never breaks the report
        logger.warning("synthesist unavailable for %s: %s", getattr(dossier, "ticker", "?"), exc)
        return None
