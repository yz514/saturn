import json
from datetime import date
from pathlib import Path

from saturn.ingestion.edgar_filings import (
    EIGHT_K_ITEM_LABELS,
    HIGH_VALUE_8K_ITEMS,
    _extract_8k,
    _extract_filing_sections,
    _parse_8k_items,
    _select_latest,
    _select_recent_8ks,
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


def _eightk_html():
    return (FIX / "eightk_excerpt.html").read_text(encoding="utf-8")


def test_parse_8k_items_splits_comma_string():
    assert _parse_8k_items("2.02,9.01") == ["2.02", "9.01"]
    assert _parse_8k_items(" 5.02 ") == ["5.02"]
    assert _parse_8k_items("") == []
    assert _parse_8k_items("2.02,") == ["2.02"]                       # trailing comma
    assert _parse_8k_items("Item 2.02,Item 9.01") == ["2.02", "9.01"]  # descriptive form


def test_select_recent_8ks_sorts_newest_first():
    subs = {
        "filings": {"recent": {
            "form": ["8-K", "8-K", "8-K"],
            "accessionNumber": ["a-1", "a-2", "a-3"],
            "primaryDocument": ["d1.htm", "d2.htm", "d3.htm"],
            "filingDate": ["2024-02-01", "2024-06-01", "2024-04-01"],
            "items": ["8.01", "2.02", "5.02"],
        }}
    }
    recent = _select_recent_8ks(subs, since=date(2024, 1, 1))
    assert [e["filing_date"] for e in recent] == ["2024-06-01", "2024-04-01", "2024-02-01"]


def test_select_recent_8ks_filters_by_window():
    subs = _submissions()
    recent = _select_recent_8ks(subs, since=date(2024, 1, 1))
    accns = {e["accession"] for e in recent}
    assert "0001045810-24-000200" in accns       # 2024-05-22 8-K kept
    assert "0001045810-24-000201" not in accns    # 2023-03-15 8-K excluded
    assert all(e["form"] == "8-K" for e in recent)
    ev = next(e for e in recent if e["accession"] == "0001045810-24-000200")
    assert ev["item_codes"] == ["2.02", "9.01"]
    assert ev["filing_date"] == "2024-05-22"


def test_extract_8k_returns_body_text():
    text = _extract_8k(_eightk_html())
    assert "record quarterly revenue" in text
    assert "<" not in text


def test_high_value_set_and_labels():
    assert "2.02" in HIGH_VALUE_8K_ITEMS
    assert EIGHT_K_ITEM_LABELS["2.02"].lower().startswith("results")
