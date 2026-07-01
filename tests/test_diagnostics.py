from datetime import date
from types import SimpleNamespace

from saturn.diagnostics import CheckResult, check_anthropic, check_edgar, check_fred, check_yfinance
from saturn.ingestion.errors import DataUnavailable
from saturn.models import (
    FinancialFact,
    Fundamentals,
    MacroSeries,
    MacroSnapshot,
    Provenance,
    Quote,
)


class _FakeClient:
    def __init__(self, api_key, default_model):
        self.default_model = default_model

    def complete(self, system, prompt, *, model=None):
        return "OK"


def test_check_anthropic_missing_key():
    r = check_anthropic(SimpleNamespace(anthropic_api_key=None))
    assert isinstance(r, CheckResult)
    assert r.name == "Anthropic"
    assert r.ok is False
    assert "ANTHROPIC_API_KEY not set" in r.detail


def test_check_anthropic_ping_ok(monkeypatch):
    monkeypatch.setattr("saturn.diagnostics.AnthropicClient", _FakeClient)
    r = check_anthropic(SimpleNamespace(anthropic_api_key="testkey"))
    assert r.ok is True
    assert "claude-haiku-4-5" in r.detail


def test_check_anthropic_error_is_caught(monkeypatch):
    class _Boom:
        def __init__(self, *a):
            raise RuntimeError("bad key")

    monkeypatch.setattr("saturn.diagnostics.AnthropicClient", _Boom)
    r = check_anthropic(SimpleNamespace(anthropic_api_key="testkey"))
    assert r.ok is False
    assert "bad key" in r.detail


def test_check_anthropic_empty_response(monkeypatch):
    class _EmptyClient:
        def __init__(self, api_key, default_model):
            pass

        def complete(self, system, prompt, *, model=None):
            return "   "

    monkeypatch.setattr("saturn.diagnostics.AnthropicClient", _EmptyClient)
    r = check_anthropic(SimpleNamespace(anthropic_api_key="testkey"))
    assert r.ok is False
    assert "empty response" in r.detail


def test_check_yfinance_ok(monkeypatch):
    monkeypatch.setattr(
        "saturn.diagnostics.fetch_quote",
        lambda ticker: Quote(price=228.5, market_cap=3_400_000_000_000.0, currency="USD", provenance=Provenance(source="yfinance")),
    )
    r = check_yfinance("AAPL")
    assert r.name == "yfinance" and r.ok is True
    assert "$228" in r.detail


def test_check_yfinance_error(monkeypatch):
    def boom(ticker):
        raise RuntimeError("network down")

    monkeypatch.setattr("saturn.diagnostics.fetch_quote", boom)
    r = check_yfinance("AAPL")
    assert r.ok is False and "network down" in r.detail


def test_check_edgar_ok(monkeypatch):
    def fake_edgar(ticker):
        return {
            "fundamentals": Fundamentals(facts=[FinancialFact(concept="Revenues", value=1.0, provenance=Provenance(source="SEC EDGAR"))]),
            "filing_sections": [],
            "material_events": [],
            "name": "Apple Inc.",
            "cik": "0000320193",
        }

    monkeypatch.setattr("saturn.diagnostics.fetch_edgar", fake_edgar)
    r = check_edgar("AAPL")
    assert r.name == "SEC EDGAR" and r.ok is True
    assert "Apple Inc." in r.detail and "0000320193" in r.detail and "1 facts" in r.detail


def test_check_edgar_data_unavailable(monkeypatch):
    def boom(ticker):
        raise DataUnavailable("SEC_USER_AGENT not set; required for SEC EDGAR access")

    monkeypatch.setattr("saturn.diagnostics.fetch_edgar", boom)
    r = check_edgar("AAPL")
    assert r.ok is False and "SEC_USER_AGENT not set" in r.detail


def test_check_fred_ok(monkeypatch):
    def fake_fred():
        return MacroSnapshot(series=[
            MacroSeries(series_id="FEDFUNDS", title="Fed Funds", observations=[(date(2026, 4, 1), 4.33)], provenance=Provenance(source="FRED")),
        ])

    monkeypatch.setattr("saturn.diagnostics.fetch_fred", fake_fred)
    r = check_fred()
    assert r.name == "FRED" and r.ok is True
    assert "FEDFUNDS" in r.detail and "1 series" in r.detail


def test_check_fred_data_unavailable(monkeypatch):
    def boom():
        raise DataUnavailable("FRED_API_KEY not set")

    monkeypatch.setattr("saturn.diagnostics.fetch_fred", boom)
    r = check_fred()
    assert r.ok is False and "FRED_API_KEY not set" in r.detail


def test_check_yfinance_no_price(monkeypatch):
    monkeypatch.setattr(
        "saturn.diagnostics.fetch_quote",
        lambda ticker: Quote(price=None, provenance=Provenance(source="yfinance")),
    )
    r = check_yfinance("AAPL")
    assert r.ok is False and "no price returned" in r.detail


from saturn.diagnostics import format_report, run_checks


def test_run_checks_returns_five(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr("saturn.diagnostics.check_anthropic", lambda s: CheckResult(name="Anthropic", ok=True, detail="x"))
    monkeypatch.setattr("saturn.diagnostics.check_yfinance", lambda t: CheckResult(name="yfinance", ok=True, detail="x"))
    monkeypatch.setattr("saturn.diagnostics.check_edgar", lambda t: CheckResult(name="SEC EDGAR", ok=True, detail="x"))
    monkeypatch.setattr("saturn.diagnostics.check_fred", lambda: CheckResult(name="FRED", ok=True, detail="x"))
    monkeypatch.setattr("saturn.diagnostics.check_consensus", lambda t: CheckResult(name="consensus", ok=True, detail="x"))
    results = run_checks("AAPL", settings=SimpleNamespace(anthropic_api_key="k"))
    assert [r.name for r in results] == ["Anthropic", "yfinance", "SEC EDGAR", "FRED", "consensus"]


def test_format_report_marks_and_summary():
    results = [
        CheckResult(name="Anthropic", ok=True, detail="key works"),
        CheckResult(name="FRED", ok=False, detail="FRED_API_KEY not set"),
    ]
    out = format_report("AAPL", results)
    assert "Saturn doctor - ticker: AAPL" in out
    assert "[OK]" in out and "[FAIL]" in out
    assert "key works" in out and "FRED_API_KEY not set" in out
    assert "1/2 checks passed." in out
    # ASCII-safe: no non-ASCII chars (Windows console)
    out.encode("ascii")


def test_format_report_coerces_non_ascii_detail():
    results = [CheckResult(name="SEC EDGAR", ok=False, detail="Société 日本 — boom")]
    out = format_report("AAPL", results)
    out.encode("ascii")  # must not raise UnicodeEncodeError
    assert "[FAIL]" in out
    assert "Soci" in out  # ASCII portion survives; non-ASCII replaced with '?'


def test_check_consensus_ok(monkeypatch):
    from saturn.diagnostics import check_consensus
    from saturn.ingestion.consensus import RawConsensus
    monkeypatch.setattr("saturn.diagnostics.fetch_consensus",
                        lambda t: RawConsensus(forward_pe=30.0, rating="buy", n_analysts=40))
    r = check_consensus("AAPL")
    assert r.ok and "forward_pe" in r.detail.lower() or r.ok


def test_check_consensus_empty(monkeypatch):
    from saturn.diagnostics import check_consensus
    from saturn.ingestion.consensus import RawConsensus
    monkeypatch.setattr("saturn.diagnostics.fetch_consensus", lambda t: RawConsensus())
    r = check_consensus("ZZZZ")
    assert not r.ok


def test_check_consensus_never_raises(monkeypatch):
    from saturn.diagnostics import check_consensus
    def boom(t):
        raise RuntimeError("yfinance down")
    monkeypatch.setattr("saturn.diagnostics.fetch_consensus", boom)
    r = check_consensus("AAPL")
    assert not r.ok and "yfinance down" in r.detail
