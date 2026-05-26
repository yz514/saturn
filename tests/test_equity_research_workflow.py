from datetime import date

from saturn.llm.mock_client import MockLLMClient
from saturn.models import CompanyData
from saturn.workflows.equity_research import _extract_json, run


def _company() -> CompanyData:
    return CompanyData(ticker="NVDA", name="NVIDIA", as_of=date(2026, 5, 25))


def test_run_with_mock_client_populates_report():
    report = run(_company(), MockLLMClient(), model_used="mock", mock=True)
    assert report.ticker == "NVDA"
    assert report.mock is True
    assert report.model_used == "mock"
    assert report.analysis.executive_summary.startswith("[MOCK]")
    assert report.debate.bull_thesis.startswith("[MOCK]")
    assert report.sources == ["MOCK fixture data — not real market sources"]


def test_run_real_mode_builds_yfinance_source():
    report = run(_company(), MockLLMClient(), model_used="claude-x", mock=False)
    assert report.sources[0] == "yfinance (price, profile, financials)"


def test_extract_json_strips_code_fences():
    fenced = '```json\n{"a": 1}\n```'
    assert _extract_json(fenced) == '{"a": 1}'
    assert _extract_json('{"a": 1}') == '{"a": 1}'
