from saturn.ingestion.dossier import _mock_dossier
from saturn.llm.mock_client import MockLLMClient
from saturn.workflows.equity_research import _extract_json, run


def test_run_with_mock_client_populates_report():
    report = run(_mock_dossier("NVDA"), MockLLMClient(), model_used="mock", mock=True)
    assert report.ticker == "NVDA"
    assert report.mock is True
    assert report.model_used == "mock"
    assert report.analysis.executive_summary.startswith("[MOCK]")
    assert report.debate.bull_thesis.startswith("[MOCK]")
    assert report.sources == ["MOCK fixture data — not real market sources"]
    assert report.critic_review is not None


def test_run_real_mode_builds_provenance_sources():
    report = run(_mock_dossier("NVDA"), MockLLMClient(), model_used="claude-x", mock=False)
    # In real mode, sources are collected from provenance fields
    assert len(report.sources) > 0
    # yfinance mock quote should appear as first source
    assert any("yfinance" in s for s in report.sources)


def test_extract_json_strips_code_fences():
    fenced = '```json\n{"a": 1}\n```'
    assert _extract_json(fenced) == '{"a": 1}'
    assert _extract_json('{"a": 1}') == '{"a": 1}'
