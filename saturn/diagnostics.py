"""Live dependency checks for `saturn doctor`.

Each check wraps one real adapter/credential and returns a CheckResult; a check
never raises. The live network calls here are intentional (this is the one
non-offline command). Unit tests monkeypatch the adapters to stay offline.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel

from saturn.ingestion.edgar import fetch_edgar
from saturn.ingestion.errors import IngestionError
from saturn.ingestion.fred import fetch_fred
from saturn.ingestion.prices import fetch_quote
from saturn.llm.anthropic_client import AnthropicClient

logger = logging.getLogger(__name__)

_PING_MODEL = "claude-haiku-4-5"  # cheapest model — this only proves the key works


class CheckResult(BaseModel):
    name: str
    ok: bool
    detail: str


def check_anthropic(settings) -> CheckResult:
    """Verify the Anthropic key by a tiny live ping on the cheapest model.

    `settings` is any object exposing `anthropic_api_key: str | None`.
    """
    if not settings.anthropic_api_key:
        return CheckResult(name="Anthropic", ok=False, detail="ANTHROPIC_API_KEY not set")
    try:
        client = AnthropicClient(settings.anthropic_api_key, _PING_MODEL)
        reply = client.complete("You are a health check.", "Reply with the single word: OK")
        if reply and reply.strip():
            return CheckResult(name="Anthropic", ok=True, detail=f"key works ({_PING_MODEL} responded)")
        return CheckResult(name="Anthropic", ok=False, detail="empty response from model")
    except Exception as exc:  # noqa: BLE001 - a check never raises
        return CheckResult(name="Anthropic", ok=False, detail=str(exc))


def _money(value: float | None) -> str:
    return f"${value:,.0f}" if value is not None else "N/A"


def check_yfinance(ticker: str) -> CheckResult:
    try:
        q = fetch_quote(ticker)
        if q.price is None:
            return CheckResult(name="yfinance", ok=False, detail="no price returned")
        return CheckResult(name="yfinance", ok=True, detail=f"price {_money(q.price)}, market cap {_money(q.market_cap)}")
    except Exception as exc:  # noqa: BLE001
        return CheckResult(name="yfinance", ok=False, detail=str(exc))


def check_edgar(ticker: str) -> CheckResult:
    try:
        r = fetch_edgar(ticker)
        fund = r.get("fundamentals")
        nfacts = len(fund.facts) if fund else 0
        nsec = len(r.get("filing_sections") or [])
        nev = len(r.get("material_events") or [])
        name = r.get("name") or ticker
        cik = r.get("cik") or "?"
        detail = f"{name} (CIK {cik}) - {nfacts} facts, {nsec} sections, {nev} events"
        return CheckResult(name="SEC EDGAR", ok=True, detail=detail)
    except IngestionError as exc:
        return CheckResult(name="SEC EDGAR", ok=False, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        return CheckResult(name="SEC EDGAR", ok=False, detail=str(exc))


def check_fred() -> CheckResult:
    try:
        snap = fetch_fred()
        if not snap.series:
            return CheckResult(name="FRED", ok=False, detail="no series returned")
        first = snap.series[0]
        latest = first.observations[-1] if first.observations else None
        example = f", e.g. {first.series_id} {round(latest[1], 2)} ({latest[0]})" if latest else ""
        return CheckResult(name="FRED", ok=True, detail=f"{len(snap.series)} series{example}")
    except IngestionError as exc:
        return CheckResult(name="FRED", ok=False, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        return CheckResult(name="FRED", ok=False, detail=str(exc))


def run_checks(ticker: str, *, settings) -> list[CheckResult]:
    """Run all dependency checks (Anthropic, yfinance, EDGAR, FRED) in order."""
    return [
        check_anthropic(settings),
        check_yfinance(ticker),
        check_edgar(ticker),
        check_fred(),
    ]


def format_report(ticker: str, results: list[CheckResult]) -> str:
    """Render an ASCII-safe readiness report (Windows-console friendly)."""
    lines = [f"Saturn doctor - ticker: {ticker}", ""]
    width = max((len(r.name) for r in results), default=0)
    for r in results:
        mark = "[OK]  " if r.ok else "[FAIL]"
        lines.append(f"{mark} {r.name.ljust(width)}  {r.detail}")
    passed = sum(1 for r in results if r.ok)
    lines.append("")
    lines.append(f"{passed}/{len(results)} checks passed.")
    return "\n".join(lines)
