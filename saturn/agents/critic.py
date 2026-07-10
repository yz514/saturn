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
