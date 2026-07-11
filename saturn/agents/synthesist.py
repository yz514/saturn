"""The Synthesist: turns analysis + debate into a structured, auditable Alpha Thesis."""
from __future__ import annotations

import json
import logging

from saturn.models import AlphaThesis, CompanyDossier, ExpectationAnchor, Provenance, ScenarioLeg

logger = logging.getLogger(__name__)
_MAX_OUTPUT_TOKENS = 8192


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
