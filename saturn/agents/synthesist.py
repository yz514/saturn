"""The Synthesist: turns analysis + debate into a structured, auditable Alpha Thesis."""
from __future__ import annotations

import json
import logging
import re

from saturn.models import AlphaThesis, CompanyDossier, ExpectationAnchor, Provenance, ScenarioLeg

logger = logging.getLogger(__name__)
_MAX_OUTPUT_TOKENS = 8192
_STANCE_BAND = 0.10  # ±10 percentage points around the consensus target defines "in line"
_COHERENCE_MULTIPLE_TOL = 0.15   # a leg multiple within ±15% of consensus forward P/E is "the forward multiple"
_COHERENCE_EPS_FLOOR = 0.8       # ... applied to an EPS below 80% of consensus forward EPS is horizon-mismatched
_PROSE_RETURN_TOL = 0.02         # prose base return may differ from the computed base return only by rounding
_PROSE_RETURN_RE = re.compile(r"base case implies[^%]*?([+-]?\d+(?:\.\d+)?)\s*%", re.IGNORECASE)
_PROSE_MATH_TOL = 0.02        # a stated A*B may differ from the true product only by rounding
_PROSE_LEG_TOL = 0.01         # a cited (value, multiple) must match a table leg this closely.
                              # NOT 2%: the real smuggled pair 18.86x19 sits 1.95% from a bear leg of
                              # 18.5x19, so a 2% tolerance would match it and defeat the check.
_PROSE_MATH_LOOKAHEAD = 120   # chars after a cited pair in which to look for its claimed price
_PROSE_PAIR_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:EPS|FCF/share|sales/share)\s*[x×]\s*(\d+(?:\.\d+)?)\s*(?:P/E|P/FCF|P/S|x)\b",
    re.IGNORECASE)
_PROSE_PRICE_RE = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)")


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
    if cons is not None and any(v is not None for v in
                                (cons.forward_pe, cons.forward_eps, cons.target_mean, cons.forward_eps_ntm)):
        px = dossier.quote.price if dossier.quote else None
        ntm_pe = (px / cons.forward_eps_ntm
                  if (px and px > 0 and cons.forward_eps_ntm and cons.forward_eps_ntm > 0) else None)
        if ntm_pe is not None:
            metric, value, unit = "NTM P/E", ntm_pe, "x"
        elif cons.forward_pe is not None:
            metric, value, unit = "Forward P/E", cons.forward_pe, "x"
        elif cons.forward_eps is not None:
            metric, value, unit = "forward EPS", cons.forward_eps, "USD/share"
        else:
            metric, value, unit = "mean price target", cons.target_mean, "USD/share"
        parts: list[str] = []
        if ntm_pe is not None:
            blend = (f"; {cons.ntm_weight:.0%} current FY / {1 - cons.ntm_weight:.0%} next FY"
                     if cons.ntm_weight is not None else "")
            parts.append(f"NTM P/E {ntm_pe:.1f}x (on blended NTM EPS ${cons.forward_eps_ntm:.2f}{blend})")
            if cons.forward_pe is not None:
                ref = f" on FY+1 EPS ${cons.forward_eps:.2f}" if cons.forward_eps is not None else ""
                parts.append(f"FY+1 P/E {cons.forward_pe:.1f}x{ref}")
        elif cons.forward_pe is not None:
            parts.append(f"forward P/E {cons.forward_pe:.1f}x")            # legacy text, unchanged
        elif cons.forward_eps is not None:
            parts.append(f"forward EPS ${cons.forward_eps:.2f}/share")     # legacy text, unchanged
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


def _prose_math_claims(text: str) -> list[tuple[float, float, float | None, int, int]]:
    """Every 'A EPS × B P/E' pair the prose asserts, each with the price it claims (when one follows
    within _PROSE_MATH_LOOKAHEAD chars) and that price NUMBER's span. Shared by the prose_arithmetic
    check and align_prose_scenario_math so they cannot drift apart. Pure; [] when there is no pair."""
    out: list[tuple[float, float, float | None, int, int]] = []
    for m in _PROSE_PAIR_RE.finditer(text):
        a, b = float(m.group(1)), float(m.group(2))
        window = text[m.end(): m.end() + _PROSE_MATH_LOOKAHEAD]
        pm = _PROSE_PRICE_RE.search(window)
        if pm:
            out.append((a, b, float(pm.group(1).replace(",", "")),
                        m.end() + pm.start(1), m.end() + pm.end(1)))
        else:
            out.append((a, b, None, -1, -1))
    return out


def scenario_coherence(thesis: AlphaThesis, dossier: CompanyDossier) -> list["CoherenceIssue"]:
    """Deterministic coherence audit of the priced scenario table (sibling to alpha_completeness).
    Returns issues in a stable order: monotonicity, prose_vs_computed, multiple_horizon,
    bull_below_spot. Pure; any missing data skips that check rather than raising."""
    from saturn.models import CoherenceIssue
    issues: list[CoherenceIssue] = []
    legs = {s.name: s for s in thesis.scenarios}
    bull, base, bear = legs.get("bull"), legs.get("base"), legs.get("bear")

    # 1. Monotonicity — bull >= base >= bear in implied price.
    if bull and base and bear and all(x.implied_price is not None for x in (bull, base, bear)):
        if not (bull.implied_price >= base.implied_price >= bear.implied_price):
            issues.append(CoherenceIssue(
                check="monotonicity", severity="high",
                detail=(f"prices not monotonic: bull ${bull.implied_price:,.2f} / "
                        f"base ${base.implied_price:,.2f} / bear ${bear.implied_price:,.2f}")))

    # 2. Prose-vs-computed — the narrated base return must match the computed base return.
    if base is not None and base.implied_return_pct is not None:
        text = thesis.rationale or thesis.variant or ""
        m = _PROSE_RETURN_RE.search(text)
        if m:
            parsed = float(m.group(1)) / 100.0
            if abs(parsed - base.implied_return_pct) > _PROSE_RETURN_TOL:
                issues.append(CoherenceIssue(
                    check="prose_vs_computed", severity="medium",
                    detail=(f"rationale says base {parsed:+.0%} but the table computes "
                            f"{base.implied_return_pct:+.0%}")))

    # 3. Multiple-horizon — a forward (FY+1) P/E applied to a materially lower near-term EPS.
    cons = dossier.consensus
    if cons is not None and cons.forward_pe is not None and cons.forward_eps is not None:
        for s in thesis.scenarios:
            if (s.multiple_basis == "P/E"
                    and abs(s.multiple - cons.forward_pe) <= _COHERENCE_MULTIPLE_TOL * cons.forward_pe
                    and s.per_share_value < _COHERENCE_EPS_FLOOR * cons.forward_eps):
                issues.append(CoherenceIssue(
                    check="multiple_horizon", severity="medium",
                    detail=(f"{s.name} applies forward P/E {s.multiple:g}x to EPS "
                            f"${s.per_share_value:g} (< {_COHERENCE_EPS_FLOOR:g}× consensus forward "
                            f"EPS ${cons.forward_eps:g})")))
                break   # one horizon issue per table is enough

    # 4. Bull-below-spot — a "bull" scenario that loses money. Unambiguously wrong unless the stance
    # is itself bearish (below_consensus), where a below-spot bull can be a deliberate short.
    if bull is not None and bull.implied_return_pct is not None and bull.implied_return_pct < 0:
        sev = "medium" if thesis.stance == "below_consensus" else "high"
        issues.append(CoherenceIssue(
            check="bull_below_spot", severity=sev,
            detail=(f"bull scenario returns {bull.implied_return_pct:+.0%} (below spot) despite a "
                    f"{thesis.stance} stance")))

    # 5. Prose arithmetic — the LLM's own "A EPS × B P/E … $C" must actually multiply out. Verifying its
    # stated math needs no whitelist: legitimately-sourced figures (spot, target, driver EPS, RPO) are
    # never touched because they are not part of an asserted pair-and-price claim.
    claims = _prose_math_claims(thesis.variant) + _prose_math_claims(thesis.rationale)
    for a, b, c, _s, _e in claims:
        if c is None:
            continue
        product = a * b
        if product > 0 and abs(product - c) / product > _PROSE_MATH_TOL:
            issues.append(CoherenceIssue(
                check="prose_arithmetic", severity="medium",
                detail=(f"prose states {a:g} × {b:g} ≈ ${c:,.2f}, but the product is ${product:,.2f}")))
            break   # one arithmetic issue per thesis is enough

    # 6. Prose scenario not in the table — a cited (value, multiple) must be one the table actually
    # prices. Catches arithmetic that is TRUE but describes a scenario we never modelled (a smuggled
    # second base case). Tolerance is deliberately tight (_PROSE_LEG_TOL): a looser 2% would match a
    # near-miss like 18.86x19 to a real 18.5x19 leg and let it through.
    legs_vm = [(s.per_share_value, s.multiple) for s in thesis.scenarios]
    for a, b, _c, _s, _e in claims:
        if not any(v > 0 and m > 0
                   and abs(v - a) / v <= _PROSE_LEG_TOL and abs(m - b) / m <= _PROSE_LEG_TOL
                   for v, m in legs_vm):
            issues.append(CoherenceIssue(
                check="prose_scenario_not_in_table", severity="medium",
                detail=f"prose cites {a:g} × {b:g}, which is not a scenario in the table"))
            break   # one orphan report per thesis is enough

    return issues


def align_prose_base_return(thesis: AlphaThesis) -> None:
    """Correct a stated base-case return in the prose to match the computed base scenario, in place.
    Mirrors deterministic stance derivation: the LLM owns the argument, code owns the number. Uses the
    same cue/tolerance as the prose_vs_computed check, so afterwards that check cannot fire on a
    parseable, divergent figure. No-ops when there is no base leg / computed return, no cue, or the
    figure is already within tolerance."""
    base = next((s for s in thesis.scenarios if s.name == "base"), None)
    if base is None or base.implied_return_pct is None:
        return
    computed = f"{base.implied_return_pct * 100:+.0f}"          # e.g. "-47" or "+12"

    def _fix(text: str) -> str:
        m = _PROSE_RETURN_RE.search(text)
        if m and abs(float(m.group(1)) / 100.0 - base.implied_return_pct) > _PROSE_RETURN_TOL:
            return _PROSE_RETURN_RE.sub(lambda mm: mm.group(0).replace(mm.group(1), computed, 1),
                                        text, count=1)
        return text

    thesis.variant = _fix(thesis.variant)
    thesis.rationale = _fix(thesis.rationale)


def align_prose_scenario_math(thesis: AlphaThesis) -> None:
    """Correct a stated scenario price in the prose to the product of the LLM's OWN cited value and
    multiple, in place. The LLM keeps its assumptions; code owns the multiplication — so a corrected
    claim lands on the table's price. Uses the same parser/tolerance as the prose_arithmetic check.
    No-ops when there is no cited pair, no claimed price, or the arithmetic is already right."""
    def _fix(text: str) -> str:
        for a, b, c, s, e in _prose_math_claims(text):
            if c is None:
                continue
            product = a * b
            if product > 0 and abs(product - c) / product > _PROSE_MATH_TOL:
                return text[:s] + f"{product:,.2f}" + text[e:]      # first bad claim; span excludes "$"
        return text

    thesis.variant = _fix(thesis.variant)
    thesis.rationale = _fix(thesis.rationale)


def apply_alpha_corrections(alpha: AlphaThesis, corrections: dict) -> AlphaThesis:
    """Splice corrected prose fields into the alpha thesis and recompute completeness. Only
    ALPHA_PROSE_FIELDS are updated; stance/stance_basis/anchor/scenarios are carried over verbatim
    by model_copy."""
    from saturn.models import ALPHA_PROSE_FIELDS
    updated = alpha.model_copy(update={k: v for k, v in corrections.items() if k in ALPHA_PROSE_FIELDS})
    updated.incompleteness = alpha_completeness(updated)
    # coherence_issues are recomputed centrally at the end of run() (needs the dossier), not here.
    return updated


SYNTHESIZE_SYSTEM = (
    "You are a portfolio manager turning an analyst's memo into a tradeable view. You are given "
    "the market-expectation ANCHOR, the draft report, and the underlying data. Write the RATIONALE "
    "around how your base-case scenario's return compares to the anchor — the consensus target "
    "upside, or the model-implied expectation — e.g. 'our base case implies +X% vs the Street's "
    "+Y%, below/above because ...', grounded in specific data. Do NOT assert an overall 'consistent "
    "with / differentiated from consensus' verdict and do NOT re-state the stance label in prose: "
    "the system derives and labels the stance deterministically from your base-case return vs "
    "consensus. "
    "When a DRIVER MODEL is present in the data, cite its gap in the variant/rationale — e.g. "
    "the Street's EPS needs revenue growth or margin the trailing trend does not support. "
    "Still return a 'stance' field — it is used only as a fallback when there is no "
    "consensus target; if you cannot take a differentiated view there, use 'unclear' and never "
    "manufacture one. "
    "Give the single key variable that decides it, an OBSERVABLE falsifier (a concrete event plus a "
    "time window), a horizon, and exactly three scenarios (bull/base/bear). Each scenario states a "
    "period, a per-share metric with its value and basis, and a multiple with its basis — do NOT "
    "output prices; the system computes price = value x multiple. Keep 'variant' to ONE sentence "
    "under 35 words. "
    "CRITICAL — horizon match: the multiple and the per-share value must be the SAME horizon. If you "
    "use a forward (next-fiscal-year) P/E, pair it with the forward EPS; NEVER apply a forward "
    "multiple to a trailing or near-term EPS — that mechanically underprices every scenario. Before "
    "finalizing, verify bull >= base >= bear in implied price and that the base-case return you "
    "describe matches the base scenario. "
    "Respond with ONLY a single valid JSON object, no prose, no code fences."
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
    elif base_return is None:
        stance_basis = "stance LLM-declared; no base-case return (no quote?)"
    else:
        stance_basis = "stance LLM-declared vs model-implied anchor; no consensus target"
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
    align_prose_base_return(thesis)
    align_prose_scenario_math(thesis)
    thesis.incompleteness = alpha_completeness(thesis)
    thesis.coherence_issues = scenario_coherence(thesis, dossier)
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


def _coherence_score(alpha: AlphaThesis) -> int:
    """Severity-weighted coherence penalty (high=2, medium=1). Lower is better; 0 is coherent."""
    return sum(2 if i.severity == "high" else 1 for i in alpha.coherence_issues)


def resynthesize_coherent(analysis, debate, dossier: CompanyDossier, llm, issues,
                          *, model: str | None = None) -> AlphaThesis | None:
    """One corrective synthesize pass: re-ask for a fully self-consistent thesis given the specific
    coherence problems. Reuses the synthesize machinery so prose AND scenarios regenerate together.
    Soft-fails to None; never breaks the report."""
    from saturn.workflows.equity_research import _company_context, _extract_json
    try:
        anchor = _resolve_anchor(dossier)
        base_prompt = _synthesize_prompt(analysis, debate, anchor, _company_context(dossier))
        problems = "; ".join(f"[{i.check}] {i.detail}" for i in issues)
        cons, dm = dossier.consensus, dossier.driver_model
        hint = ""
        if cons and cons.forward_pe and cons.forward_eps and dm and dm.saturn_eps:
            hint = (f" For reference, the stock trades at ~{cons.forward_pe:.0f}x its forward EPS "
                    f"${cons.forward_eps:.2f} (which equals spot). Applying ~{cons.forward_pe:.0f}x "
                    f"to a near-term EPS like ${dm.saturn_eps:.2f} yields a price far below spot — "
                    f"that is the horizon error. Either pair forward EPS with the forward multiple, "
                    f"or use a near-term multiple (~15-20x) with the near-term EPS.")
        corrective = (
            "\n\nYour previous scenario table failed these coherence checks: " + problems + ". "
            "Regenerate the FULL thesis so that: bull >= base >= bear in implied price; any P/E "
            "multiple matches the horizon of its EPS (do NOT apply a next-fiscal-year multiple to a "
            "near-term EPS); and the base-case return you describe in the rationale matches the base "
            "scenario you output. Do NOT output prices." + hint
        )
        strict = ("\n\nIMPORTANT: your previous reply was not valid JSON. Return ONLY a single, "
                  "strictly valid JSON object.")
        for attempt in range(2):
            raw = llm.complete(SYNTHESIZE_SYSTEM,
                               base_prompt + corrective + ("" if attempt == 0 else strict),
                               model=model, max_tokens=_MAX_OUTPUT_TOKENS)
            try:
                return _build_thesis(json.loads(_extract_json(raw)), anchor, dossier)
            except Exception:  # noqa: BLE001 - malformed JSON; retry once then give up
                continue
        logger.warning("scenario re-synthesis unparseable after retry for %s", getattr(dossier, "ticker", "?"))
        return None
    except Exception as exc:  # noqa: BLE001 - best-effort; never breaks the report
        logger.warning("scenario re-synthesis unavailable for %s: %s", getattr(dossier, "ticker", "?"), exc)
        return None
