import json
from pathlib import Path

from saturn.ingestion.edgar_filings import (
    _extract_filing_sections,
    _select_latest,
    _strip_html,
)

FIX = Path(__file__).parent.parent / "fixtures" / "edgar"


def _submissions():
    return json.loads((FIX / "submissions_NVDA.json").read_text(encoding="utf-8"))


def _tenk_html():
    return (FIX / "tenk_excerpt.html").read_text(encoding="utf-8")


def test_strip_html_removes_tags_and_unescapes():
    text = _strip_html("<p>A &amp; B</p><p>C</p>")
    assert "A & B" in text
    assert "<" not in text


def test_strip_html_drops_script_and_style_content():
    text = _strip_html("<style>.x{color:red}</style><p>Hello</p><script>var a=1;</script>")
    assert "Hello" in text
    assert "color" not in text
    assert "var a" not in text


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


def test_select_latest_picks_most_recent_for_form():
    sel = _select_latest(_submissions(), "10-K")
    assert sel["accession"] == "0001045810-24-000029"
    assert sel["primary_document"] == "nvda-20240128.htm"
    assert sel["filing_date"] == "2024-02-21"
    assert sel["report_date"] == "2024-01-28"


def test_select_latest_returns_none_when_absent():
    empty = {"filings": {"recent": {"accessionNumber": [], "form": [], "filingDate": [], "primaryDocument": []}}}
    assert _select_latest(empty, "10-K") is None
