import json
from pathlib import Path

from saturn.ingestion.edgar import _parse_companyfacts
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
