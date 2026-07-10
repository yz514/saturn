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


def test_instant_concept_quarter_picks_period_end_not_comparative():
    """A 10-Q balance sheet carries the current quarter-end value AND a prior-
    period comparative under the same (fy, fp) key. We must keep the quarter-end
    value, not let the year-end comparative repeat across quarters."""
    payload = {
        "cik": 1045810,
        "facts": {"us-gaap": {"AssetsCurrent": {"units": {"USD": [
            # prior fiscal year-end comparative, listed FIRST, same fy/fp/filed
            {"end": "2025-01-31", "val": 300, "fy": 2025, "fp": "Q3", "form": "10-Q", "filed": "2025-11-20"},
            # the genuine Q3 quarter-end balance
            {"end": "2025-10-31", "val": 500, "fy": 2025, "fp": "Q3", "form": "10-Q", "filed": "2025-11-20"},
        ]}}}},
    }
    f = _parse_companyfacts(payload)
    q3 = next(x for x in f.facts if x.concept == "AssetsCurrent" and x.fiscal_period == "Q3 FY2025")
    assert q3.value == 500  # quarter-end, not the 300 year-end comparative


def test_instant_concept_annual_picks_year_end_not_comparative():
    """A 10-K balance sheet carries the current and prior year-end under fy=2024;
    keep the current year-end (later `end`)."""
    payload = {
        "cik": 1045810,
        "facts": {"us-gaap": {"Assets": {"units": {"USD": [
            {"end": "2023-12-31", "val": 800, "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2025-02-15"},
            {"end": "2024-12-31", "val": 1000, "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2025-02-15"},
        ]}}}},
    }
    f = _parse_companyfacts(payload)
    fy = next(x for x in f.facts if x.concept == "Assets" and x.fiscal_period == "FY2024")
    assert fy.value == 1000


def test_quarterly_flow_picks_three_month_not_ytd():
    """A 10-Q tags each flow concept with both a 3-month and a year-to-date
    duration sharing (fy, fp) and the same `end`. Keep the single-quarter value."""
    payload = {
        "cik": 1045810,
        "facts": {"us-gaap": {"RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
            # YTD (6-month) cumulative, listed FIRST
            {"start": "2024-12-01", "end": "2025-05-31", "val": 250, "fy": 2025, "fp": "Q2", "form": "10-Q", "filed": "2025-06-20"},
            # the single-quarter (3-month) value we want
            {"start": "2025-03-01", "end": "2025-05-31", "val": 100, "fy": 2025, "fp": "Q2", "form": "10-Q", "filed": "2025-06-20"},
        ]}}}},
    }
    f = _parse_companyfacts(payload)
    q2 = next(x for x in f.facts if x.concept == "Revenues" and x.fiscal_period == "Q2 FY2025")
    assert q2.value == 100  # 3-month, not the 250 YTD cumulative


def test_quarterly_ytd_only_flow_is_dropped_not_mislabeled():
    """Cash-flow items are reported YTD-only in 10-Qs. Rather than present a
    cumulative 6/9-month figure as a single quarter, we omit the quarterly fact
    (the annual figure still flows through). This avoids double-counting."""
    payload = {
        "cik": 1045810,
        "facts": {"us-gaap": {"NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [
            # only a YTD (6-month) duration exists for this quarter
            {"start": "2024-12-01", "end": "2025-05-31", "val": 250, "fy": 2025, "fp": "Q2", "form": "10-Q", "filed": "2025-06-20"},
        ]}}}},
    }
    f = _parse_companyfacts(payload)
    q = [x for x in f.facts if x.concept == "OperatingCashFlow" and (x.fiscal_period or "").startswith("Q")]
    assert q == []  # YTD-only quarterly row dropped, not mislabeled as a single quarter


def test_parse_includes_debt_current_and_receivables():
    payload = {
        "cik": 1045810,
        "facts": {"us-gaap": {
            "DebtCurrent": {"units": {"USD": [
                {"end": "2025-12-31", "val": 1_000, "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2026-02-15"},
            ]}},
            "AccountsReceivableNetCurrent": {"units": {"USD": [
                {"end": "2025-12-31", "val": 2_000, "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2026-02-15"},
            ]}},
        }},
    }
    f = _parse_companyfacts(payload)
    concepts = {x.concept for x in f.facts}
    assert "DebtCurrent" in concepts
    assert "AccountsReceivableNetCurrent" in concepts


def test_annual_keyed_by_period_end_not_filing_fy():
    """Mirrors AVGO NetIncomeLoss: three fiscal years all tagged with the FILING's
    fy=2024 but with distinct period ends, plus a quarterly figure mistagged fp=FY
    inside the 10-K. We must recover all three years (keyed by period end) and drop
    the mistagged quarter (excluded by its ~90-day span)."""
    payload = {"cik": 1, "facts": {"us-gaap": {"NetIncomeLoss": {"units": {"USD": [
        {"start": "2021-11-01", "end": "2022-10-30", "val": 11495, "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2024-12-20"},
        {"start": "2022-10-31", "end": "2023-10-29", "val": 14082, "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2024-12-20"},
        {"start": "2023-10-30", "end": "2024-11-03", "val": 5895, "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2024-12-20"},
        {"start": "2024-08-01", "end": "2024-11-03", "val": 1500, "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2024-12-20"},  # mistagged quarter
    ]}}}}}
    f = _parse_companyfacts(payload, max_years=5)
    ni = {x.fiscal_period: x.value for x in f.facts if x.concept == "NetIncomeLoss"}
    assert ni.get("FY2022") == 11495
    assert ni.get("FY2023") == 14082
    assert ni.get("FY2024") == 5895   # the ~annual row, not the 1500 mistagged quarter


def test_alias_tags_merge_by_period_to_fill_gaps():
    """First-present-tag-wins drops recent data when a filer migrates to an alias
    tag (e.g. AVGO net income under ProfitLoss, equity under the ...IncludingNCI
    tag). Alias tags must be MERGED per fiscal period, with the primary tag winning
    any overlapping year."""
    payload = {"cik": 1, "facts": {"us-gaap": {
        "NetIncomeLoss": {"units": {"USD": [
            {"start": "2022-10-31", "end": "2023-10-29", "val": 14082, "fp": "FY", "form": "10-K", "filed": "2023-12-20"},
        ]}},
        "ProfitLoss": {"units": {"USD": [
            {"start": "2023-10-30", "end": "2024-11-03", "val": 5895, "fp": "FY", "form": "10-K", "filed": "2024-12-20"},
            {"start": "2022-10-31", "end": "2023-10-29", "val": 99999, "fp": "FY", "form": "10-K", "filed": "2024-12-20"},
        ]}},
    }}}
    f = _parse_companyfacts(payload, max_years=5)
    ni = {x.fiscal_period: x.value for x in f.facts if x.concept == "NetIncomeLoss"}
    assert ni.get("FY2024") == 5895    # gap filled from the ProfitLoss alias
    assert ni.get("FY2023") == 14082   # primary NetIncomeLoss tag wins the overlap


def test_annual_instant_keyed_by_end_recovers_all_years():
    """Instant balance-sheet rows (no `start`) tagged with the filing fy must still
    key by their own period end so every fiscal-year-end value is recovered."""
    payload = {"cik": 1, "facts": {"us-gaap": {"StockholdersEquity": {"units": {"USD": [
        {"end": "2023-10-29", "val": 700, "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2024-12-20"},
        {"end": "2024-11-03", "val": 800, "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2024-12-20"},
    ]}}}}}
    f = _parse_companyfacts(payload, max_years=5)
    eq = {x.fiscal_period: x.value for x in f.facts if x.concept == "StockholdersEquity"}
    assert eq.get("FY2023") == 700
    assert eq.get("FY2024") == 800


def _cf_migrated_interest():
    # Plain InterestExpense stops at FY2023; recent years only under InterestExpenseNonoperating.
    def _fy(y, val):
        return {"start": f"{y}-01-01", "end": f"{y}-12-31", "val": val, "fy": y, "fp": "FY",
                "form": "10-K", "filed": f"{y+1}-01-15"}
    return {"cik": 723125, "facts": {"us-gaap": {
        "InterestExpense": {"units": {"USD": [_fy(2022, 189_000_000), _fy(2023, 388_000_000)]}},
        "InterestExpenseNonoperating": {"units": {"USD": [
            {"start": "2023-01-01", "end": "2023-12-31", "val": 999, "fy": 2023, "fp": "FY", "form": "10-K", "filed": "2024-01-15"},
            _fy(2024, 562_000_000), _fy(2025, 477_000_000)]}},
    }}}


def test_interest_expense_alias_captures_migrated_tag():
    f = _parse_companyfacts(_cf_migrated_interest(), max_years=4)
    ie = {x.fiscal_period: x.value for x in f.facts if x.concept == "InterestExpense"}
    assert ie.get("FY2025") == 477_000_000       # recovered from InterestExpenseNonoperating
    assert ie.get("FY2024") == 562_000_000
    assert ie.get("FY2023") == 388_000_000        # plain tag wins the overlap (not 999)


def test_ppe_and_interest_aliases_registered():
    from saturn.ingestion.edgar import EDGAR_CONCEPTS
    assert "InterestExpenseNonoperating" in EDGAR_CONCEPTS["InterestExpense"]["tags"]
    assert ("PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization"
            in EDGAR_CONCEPTS["PropertyPlantAndEquipmentNet"]["tags"])


def test_long_term_debt_alias_captures_migrated_tag():
    # MU's recent long-term debt (Q3 FY2026 $5.140B) lives under the combined
    # LongTermDebtAndCapitalLeaseObligations tag, not LongTermDebtNoncurrent.
    payload = {"cik": 723125, "facts": {"us-gaap": {
        "LongTermDebtNoncurrent": {"units": {"USD": [
            {"end": "2025-08-28", "val": 8_800_000_000, "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-10-01"},
        ]}},
        "LongTermDebtAndCapitalLeaseObligations": {"units": {"USD": [
            {"end": "2026-05-28", "val": 5_140_000_000, "fy": 2026, "fp": "Q3", "form": "10-Q", "filed": "2026-07-01"},
        ]}},
    }}}
    f = _parse_companyfacts(payload)
    ltd = {x.fiscal_period: x.value for x in f.facts if x.concept == "LongTermDebt"}
    assert ltd.get("Q3 FY2026") == 5_140_000_000   # recovered from the migrated tag


def test_long_term_debt_alias_registered():
    from saturn.ingestion.edgar import EDGAR_CONCEPTS
    assert "LongTermDebtAndCapitalLeaseObligations" in EDGAR_CONCEPTS["LongTermDebt"]["tags"]


def test_fetch_edgar_includes_quarterly_mdna_and_events(monkeypatch):
    from datetime import date as _date

    cf = _companyfacts()
    sub = _submissions()  # has a 10-Q and two 8-Ks
    tenk = (FIX / "tenk_excerpt.html").read_text(encoding="utf-8")
    eightk = (FIX / "eightk_excerpt.html").read_text(encoding="utf-8")
    tenq = (FIX / "tenq_excerpt.html").read_text(encoding="utf-8")

    monkeypatch.setattr("saturn.ingestion.edgar.ticker_to_cik", lambda t: "0001045810")
    monkeypatch.setattr("saturn.ingestion.edgar._fetch_companyfacts", lambda cik: cf)
    monkeypatch.setattr("saturn.ingestion.edgar._fetch_submissions", lambda cik: sub)

    def fake_html(cik, accn, doc):
        if doc.startswith("ev-"):
            return eightk
        if doc == "nvda-20240428.htm":   # the 10-Q primary document
            return tenq
        return tenk

    monkeypatch.setattr("saturn.ingestion.edgar._fetch_filing_html", fake_html)
    monkeypatch.setattr("saturn.ingestion.edgar._cache_full_text", lambda *a, **k: "cache://ref")
    monkeypatch.setattr(
        "saturn.ingestion.edgar._select_recent_8ks",
        lambda submissions, *, since: [
            {
                "form": "8-K",
                "accession": "0001045810-24-000200",
                "primary_document": "ev-20240522.htm",
                "filing_date": "2024-05-22",
                "item_codes": ["2.02", "9.01"],
            }
        ],
    )

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


def test_finance_lease_principal_concept_registered():
    from saturn.ingestion.edgar import EDGAR_CONCEPTS
    assert "FinanceLeasePrincipalPayments" in EDGAR_CONCEPTS
    assert "FinanceLeasePrincipalPayments" in EDGAR_CONCEPTS["FinanceLeasePrincipalPayments"]["tags"]


def test_fetch_edgar_appends_segment_section(monkeypatch):
    cf, sub = _companyfacts(), _submissions()
    earnings = {"form": "8-K", "accession": "0000723125-26-000013", "primary_document": "x.htm",
                "filing_date": "2026-06-24", "item_codes": ["2.02", "9.01"]}
    monkeypatch.setattr("saturn.ingestion.edgar.ticker_to_cik", lambda t: "0001045810")
    monkeypatch.setattr("saturn.ingestion.edgar._fetch_companyfacts", lambda cik: cf)
    monkeypatch.setattr("saturn.ingestion.edgar._fetch_submissions", lambda cik: sub)
    monkeypatch.setattr("saturn.ingestion.edgar._cache_full_text", lambda *a, **k: "cache://ref")
    monkeypatch.setattr("saturn.ingestion.edgar._select_recent_8ks", lambda sub, since: [earnings])
    monkeypatch.setattr("saturn.ingestion.edgar._fetch_filing_index",
                        lambda cik, accn: [{"name": "ex991-press.htm"}])
    # segment fetch reads the exhibit HTML; other _fetch_filing_html calls (10-K/10-Q) return the tenk fixture
    def _html(cik, accn, doc):
        if doc == "ex991-press.htm":
            return "<p>Quarterly Business Unit Financial Results: Cloud Memory Revenue $13,769 Gross margin 83%</p>"
        return _tenk_html()
    monkeypatch.setattr("saturn.ingestion.edgar._fetch_filing_html", _html)
    sections = fetch_edgar("MU")["filing_sections"]
    seg = next((s for s in sections if s.name == "Business Unit / Segment Results (earnings release)"), None)
    assert seg is not None and "Cloud Memory" in seg.excerpt
    assert seg.provenance.source_url.endswith("ex991-press.htm")


def test_fetch_edgar_no_segment_section_when_no_exhibit(monkeypatch):
    cf, sub = _companyfacts(), _submissions()
    monkeypatch.setattr("saturn.ingestion.edgar.ticker_to_cik", lambda t: "0001045810")
    monkeypatch.setattr("saturn.ingestion.edgar._fetch_companyfacts", lambda cik: cf)
    monkeypatch.setattr("saturn.ingestion.edgar._fetch_submissions", lambda cik: sub)
    monkeypatch.setattr("saturn.ingestion.edgar._fetch_filing_html", lambda cik, accn, doc: _tenk_html())
    monkeypatch.setattr("saturn.ingestion.edgar._cache_full_text", lambda *a, **k: "ref")
    monkeypatch.setattr("saturn.ingestion.edgar._select_recent_8ks",
                        lambda sub, since: [{"accession": "a", "primary_document": "x.htm",
                        "filing_date": "2026-06-24", "item_codes": ["2.02"]}])
    monkeypatch.setattr("saturn.ingestion.edgar._fetch_filing_index", lambda cik, accn: [{"name": "R1.htm"}])
    sections = fetch_edgar("MU")["filing_sections"]
    assert not any(s.name.startswith("Business Unit / Segment") for s in sections)
