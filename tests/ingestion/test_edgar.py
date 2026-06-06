import json
from pathlib import Path

from saturn.ingestion.edgar import _extract_filing_sections, _parse_companyfacts, _select_latest_10k, _strip_html
from saturn.models import Fundamentals

FIX = Path(__file__).parent.parent / "fixtures" / "edgar"


def _companyfacts():
    return json.loads((FIX / "companyfacts_NVDA.json").read_text(encoding="utf-8"))


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


def _submissions():
    return json.loads((FIX / "submissions_NVDA.json").read_text(encoding="utf-8"))


def test_select_latest_10k_picks_most_recent_annual():
    sel = _select_latest_10k(_submissions())
    assert sel["accession"] == "0001045810-24-000029"
    assert sel["primary_document"] == "nvda-20240128.htm"
    assert sel["filing_date"] == "2024-02-21"
    assert sel["report_date"] == "2024-01-28"


def test_select_latest_10k_returns_none_when_absent():
    empty = {"filings": {"recent": {"accessionNumber": [], "form": [], "filingDate": [], "primaryDocument": []}}}
    assert _select_latest_10k(empty) is None


def _tenk_html():
    return (FIX / "tenk_excerpt.html").read_text(encoding="utf-8")


def test_strip_html_removes_tags_and_unescapes():
    text = _strip_html("<p>A &amp; B</p><p>C</p>")
    assert "A & B" in text
    assert "<" not in text


def test_extract_sections_returns_named_bodies():
    sections = _extract_filing_sections(_tenk_html())
    names = {s["name"] for s in sections}
    assert {"Business", "Risk Factors", "Management Discussion & Analysis"} <= names


def test_extracted_risk_factors_has_real_body_not_toc_link():
    sections = _extract_filing_sections(_tenk_html())
    rf = next(s for s in sections if s["name"] == "Risk Factors")
    assert "Demand for our products" in rf["text"]
    assert len(rf["text"]) > 40


def test_extract_sections_empty_when_no_items():
    assert _extract_filing_sections("<html><body><p>nothing here</p></body></html>") == []


def test_strip_html_drops_script_and_style_content():
    text = _strip_html("<style>.x{color:red}</style><p>Hello</p><script>var a=1;</script>")
    assert "Hello" in text
    assert "color" not in text
    assert "var a" not in text


from saturn.ingestion.edgar import fetch_edgar
from saturn.models import FilingSection


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
