"""Guidance extraction: read management's DISCLOSED forward revenue guidance from filing text.

The LLM extracts a STATED figure (not a forecast); Saturn accepts it only when the cited quote is
found verbatim in the ingested filing text, so a fabricated guide is discarded (falls back to the
trailing-trend baseline).
"""
from __future__ import annotations

import json
import logging
from datetime import date

from saturn.models import Guidance, Provenance

logger = logging.getLogger(__name__)
_MAX_OUTPUT_TOKENS = 2048
_MAX_FILING_CHARS = 8000   # keep the prompt within the context budget
_GROWTH_BOUNDS = (-0.9, 2.0)   # discard an implied growth outside this (mis-scaled figure)

GUIDANCE_SYSTEM = (
    "You extract management's DISCLOSED forward revenue guidance from an earnings release / 8-K. "
    "Report only a figure management EXPLICITLY stated, with the verbatim sentence — do NOT infer "
    "or forecast. If there is no explicit forward revenue guidance, return an empty object {}."
)


def _norm(s: str) -> str:
    return " ".join((s or "").split())


def extract_guidance(dossier, llm, *, model: str | None = None) -> Guidance | None:
    """Return grounded forward revenue Guidance, or None (soft-fail / not disclosed / ungrounded)."""
    from saturn.workflows.equity_research import _extract_json
    from saturn.analytics.metrics import _index, _ttm_or_fy
    try:
        source = " ".join((s.excerpt or "") for s in dossier.filing_sections)
        if not source.strip():
            return None
        prompt = (
            "OUTPUT_SCHEMA=guidance\n"
            "FILING TEXT (earnings release / 8-K):\n" + source[:_MAX_FILING_CHARS] + "\n\n"
            "Return ONLY: {\"value\": number (guided revenue, same scale as reported revenue, e.g. "
            "50000000000 for $50B), \"period\": \"FY\" or \"quarter\", \"quote\": \"verbatim sentence\"} "
            "or {} if no explicit forward revenue guidance."
        )
        strict = "\n\nIMPORTANT: your previous reply was not valid JSON. Return ONLY a single JSON object."
        data = None
        for attempt in range(2):
            raw = llm.complete(GUIDANCE_SYSTEM, prompt if attempt == 0 else prompt + strict,
                               model=model, max_tokens=_MAX_OUTPUT_TOKENS)
            try:
                data = json.loads(_extract_json(raw))
                break
            except Exception:  # noqa: BLE001 - malformed JSON; retry once
                continue
        if not isinstance(data, dict) or "value" not in data or "quote" not in data:
            return None

        try:
            value = float(data["value"])
        except (TypeError, ValueError):
            logger.info("guidance discarded (non-numeric value %r) for %s",
                        data.get("value"), getattr(dossier, "ticker", "?"))
            return None
        quote = str(data.get("quote") or "")
        period = "quarter" if str(data.get("period", "FY")).lower().startswith("q") else "FY"

        # grounding gate: the verbatim quote must appear in the ingested filing text
        if not quote or _norm(quote) not in _norm(source):
            logger.info("guidance discarded (quote not grounded) for %s", getattr(dossier, "ticker", "?"))
            return None

        rev = _ttm_or_fy(_index(dossier.fundamentals), "Revenues")
        if rev is None or rev[0] <= 0:
            return None
        base = value * 4 if period == "quarter" else value
        implied_growth = base / rev[0] - 1
        if not (_GROWTH_BOUNDS[0] <= implied_growth <= _GROWTH_BOUNDS[1]):
            logger.info("guidance discarded (implied growth %.2f out of bounds) for %s",
                        implied_growth, getattr(dossier, "ticker", "?"))
            return None

        return Guidance(metric="revenue", period=period, value=value, implied_growth=implied_growth,
                        quote=quote, provenance=Provenance(source="SEC EDGAR (guidance)", as_of=date.today()))
    except Exception as exc:  # noqa: BLE001 - guidance is best-effort; never breaks the report
        logger.warning("guidance extraction unavailable for %s: %s", getattr(dossier, "ticker", "?"), exc)
        return None
