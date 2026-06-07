import json
from pathlib import Path

from saturn.ingestion.edgar import (
    _parse_companyfacts,
    fetch_edgar,
)
from saturn.models import FilingSection, Fundamentals

FIX = Path(__file__).parent.parent / "fixtures" / "edgar"


def _companyfacts():
    return json.loads((FIX / "companyfacts_NVDA.json").read_text(encoding="utf-8"))


def _submissions():
    return json.loads((FIX / "submissions_NVDA.json").read_text(encoding="utf-8"))


def _tenk_html():
    return (FIX / "tenk_excerpt.html").read_text(encoding="utf-8")


def test_parse_returns_fundamentals_with_annual_facts():
    f = _parse_companyfacts(_companyfacts(), max_years=4)
    assert isinstance(f, Fundamentals)
    revs = [x for x in f.facts if x.concept == "Revenues"]
    periods = sorted(x.fiscal_period for x in revs)
    assert periods == ["FY2023", "FY2024"]


def test_latest_filing_wins_for_duplicate_year():
    f = _parse_companyfacts(_companyfacts(), max_years=4)
    fy2024 = next(x for x in f.facts if x.concept == "Revenues" and x.fiscal_period == "FY2024")
    assert fy2024.value == 60900000000
    assert fy2024.provenance.as_of.isoformat() == "2024-03-01"


def test_facts_carry_usd_unit_and_edgar_provenance():
    f = _parse_companyfacts(_companyfacts())
    fact = f.facts[0]
    assert fact.unit == "USD"
    assert fact.provenance.source == "SEC EDGAR"


def test_quarterly_and_non_10k_entries_are_excluded():
    f = _parse_companyfacts(_companyfacts())
    assert all(x.fiscal_period.startswith("FY") for x in f.facts)
    assert all(x.value not in (18120000000,) for x in f.facts)


def test_max_years_limits_history():
    f = _parse_companyfacts(_companyfacts(), max_years=1)
    revs = [x for x in f.facts if x.concept == "Revenues"]
    assert [x.fiscal_period for x in revs] == ["FY2024"]


def test_parse_empty_payload_returns_no_facts():
    from saturn.ingestion.edgar import _parse_companyfacts as parse
    assert parse({}).facts == []
    assert parse({"facts": {}}).facts == []


def test_fetch_edgar_assembles_dossier_dict(monkeypatch):
    cf = _companyfacts()
    sub = _submissions()
    html = _tenk_html()

    monkeypatch.setattr("saturn.ingestion.edgar.ticker_to_cik", lambda t: "0001045810")
    monkeypatch.setattr("saturn.ingestion.edgar._fetch_companyfacts", lambda cik: cf)
    monkeypatch.setattr("saturn.ingestion.edgar._fetch_submissions", lambda cik: sub)
    monkeypatch.setattr("saturn.ingestion.edgar._fetch_filing_html", lambda cik, accn, doc: html)
    monkeypatch.setattr("saturn.ingestion.edgar._cache_full_text", lambda *a, **k: "cache://ref")

    result = fetch_edgar("NVDA")
    assert result["cik"] == "0001045810"
    assert result["name"] == "NVIDIA CORP"
    assert any(f.concept == "Revenues" for f in result["fundamentals"].facts)
    sections = result["filing_sections"]
    assert all(isinstance(s, FilingSection) for s in sections)
    rf = next(s for s in sections if s.name == "Risk Factors")
    assert rf.provenance.source == "SEC EDGAR"
    assert rf.full_text_cache_ref == "cache://ref"
    assert len(rf.excerpt) <= 4000


def test_fetch_edgar_unknown_ticker_propagates_data_unavailable(monkeypatch):
    from saturn.ingestion.errors import DataUnavailable
    import pytest

    def no_cik(t):
        raise DataUnavailable(f"no CIK for {t}")

    monkeypatch.setattr("saturn.ingestion.edgar.ticker_to_cik", no_cik)
    with pytest.raises(DataUnavailable):
        fetch_edgar("ZZZZ")


def test_ua_requires_sec_user_agent():
    import pytest
    from saturn.ingestion.edgar import _ua
    from saturn.ingestion.errors import DataUnavailable

    # The autouse offline fixture drops SEC_USER_AGENT, so _ua() must raise.
    with pytest.raises(DataUnavailable):
        _ua()


def test_parse_captures_non_usd_units():
    f = _parse_companyfacts(_companyfacts())
    eps = next((x for x in f.facts if x.concept == "EarningsPerShareDiluted"), None)
    shares = next((x for x in f.facts if x.concept == "WeightedAverageSharesDiluted"), None)
    assert eps is not None and eps.unit == "USD/shares" and eps.value == 11.93
    assert shares is not None and shares.unit == "shares" and shares.value == 2494000000


def test_parse_includes_expanded_concepts():
    f = _parse_companyfacts(_companyfacts())
    concepts = {x.concept for x in f.facts}
    assert {"CostOfRevenue", "AssetsCurrent", "CapitalExpenditures"} <= concepts
    capex = next(x for x in f.facts if x.concept == "CapitalExpenditures")
    assert capex.unit == "USD" and capex.value == 1069000000
