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
    revs = [x for x in f.facts if x.concept == "Revenues" and x.fiscal_period.startswith("FY")]
    periods = sorted(x.fiscal_period for x in revs)
    assert periods == ["FY2023", "FY2024"]


def test_latest_filing_wins_for_duplicate_year():
    f = _parse_companyfacts(_companyfacts(), max_years=4)
    fy2024 = next(x for x in f.facts if x.concept == "Revenues" and x.fiscal_period == "FY2024")
    assert fy2024.value == 60900000000
    assert fy2024.provenance.as_of.isoformat() == "2024-03-01"


def test_facts_carry_usd_unit_and_edgar_provenance():
    f = _parse_companyfacts(_companyfacts())
    fact = next(x for x in f.facts if x.concept == "Revenues")
    assert fact.unit == "USD"
    assert fact.provenance.source == "SEC EDGAR"


def test_annual_entries_included_and_non_annual_non_quarterly_excluded():
    f = _parse_companyfacts(_companyfacts())
    # Annual facts still present
    assert any(x.fiscal_period == "FY2024" and x.concept == "Revenues" for x in f.facts)
    assert any(x.fiscal_period == "Q3 FY2024" and x.concept == "Revenues" for x in f.facts)


def test_max_years_limits_history():
    f = _parse_companyfacts(_companyfacts(), max_years=1)
    revs = [x for x in f.facts if x.concept == "Revenues" and x.fiscal_period.startswith("FY")]
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


def test_parse_emits_quarterly_facts():
    f = _parse_companyfacts(_companyfacts())
    q = [x for x in f.facts if x.concept == "Revenues" and x.fiscal_period.startswith("Q")]
    periods = {x.fiscal_period for x in q}
    assert {"Q1 FY2025", "Q2 FY2025"} <= periods
    q2 = next(x for x in f.facts if x.fiscal_period == "Q2 FY2025" and x.concept == "Revenues")
    assert q2.value == 30040000000
    # annual still present
    assert any(x.fiscal_period == "FY2024" and x.concept == "Revenues" for x in f.facts)


def test_quarterly_cap_respected():
    f = _parse_companyfacts(_companyfacts(), max_quarters=1)
    q = [x for x in f.facts if x.concept == "Revenues" and x.fiscal_period.startswith("Q")]
    assert len(q) == 1
    assert q[0].fiscal_period == "Q2 FY2025"  # most recent quarter


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


def test_fetch_edgar_includes_quarterly_mdna_and_events(monkeypatch):
    from datetime import date as _date

    cf = _companyfacts()
    sub = _submissions()  # has a 10-Q and two 8-Ks
    tenk = (FIX / "tenk_excerpt.html").read_text(encoding="utf-8")
    eightk = (FIX / "eightk_excerpt.html").read_text(encoding="utf-8")

    monkeypatch.setattr("saturn.ingestion.edgar.ticker_to_cik", lambda t: "0001045810")
    monkeypatch.setattr("saturn.ingestion.edgar._fetch_companyfacts", lambda cik: cf)
    monkeypatch.setattr("saturn.ingestion.edgar._fetch_submissions", lambda cik: sub)

    def fake_html(cik, accn, doc):
        return eightk if doc.startswith("ev-") else tenk

    monkeypatch.setattr("saturn.ingestion.edgar._fetch_filing_html", fake_html)
    monkeypatch.setattr("saturn.ingestion.edgar._cache_full_text", lambda *a, **k: "cache://ref")

    result = fetch_edgar("NVDA")

    # 8-K material events present, newest first, high-value item carries an excerpt
    events = result["material_events"]
    assert events, "expected material events"
    ev = events[0]
    assert isinstance(ev.filing_date, _date)          # converted from ISO string to date
    assert ev.filing_date.isoformat() == "2024-05-22"  # newest in-window 8-K
    assert "2.02" in ev.item_codes
    assert ev.excerpt and "record quarterly revenue" in ev.excerpt
    assert ev.provenance.source == "SEC EDGAR"

    # quarterly MD&A appended as a FilingSection with the 10-Q's filing date
    mdna_dates = [
        s.provenance.as_of
        for s in result["filing_sections"]
        if s.name == "Management Discussion & Analysis"
    ]
    assert any(d is not None and d.isoformat() == "2024-05-29" for d in mdna_dates)
