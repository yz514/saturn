import json

from saturn.llm.mock_client import MockLLMClient


def test_mock_returns_analysis_json():
    client = MockLLMClient()
    raw = client.complete("sys", "OUTPUT_SCHEMA=analysis\nplease analyze")
    data = json.loads(raw)
    assert set(data) == {
        "executive_summary",
        "company_overview",
        "business_segments",
        "financial_snapshot",
        "valuation_discussion",
        "key_risks",
        "open_questions",
    }
    assert data["executive_summary"].startswith("[MOCK]")


def test_mock_returns_debate_json():
    client = MockLLMClient()
    raw = client.complete("sys", "OUTPUT_SCHEMA=debate\nplease debate")
    data = json.loads(raw)
    assert set(data) == {"bull_thesis", "bear_thesis", "final_view"}
    assert data["bull_thesis"].startswith("[MOCK]")


def test_mock_is_deterministic():
    client = MockLLMClient()
    a = client.complete("s", "OUTPUT_SCHEMA=analysis")
    b = client.complete("s", "OUTPUT_SCHEMA=analysis")
    assert a == b
