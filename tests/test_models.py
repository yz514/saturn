from datetime import date

from saturn.models import (
    CompanyDossier,
    FinancialFact,
    Fundamentals,
    Provenance,
    Quote,
    SourceGap,
)


def test_provenance_defaults_optional():
    p = Provenance(source="FRED")
    assert p.source == "FRED"
    assert p.source_url is None and p.as_of is None and p.retrieved_at is None


def test_financial_fact_carries_provenance():
    fact = FinancialFact(
        concept="Revenues",
        value=1000.0,
        unit="USD",
        fiscal_period="FY2024",
        provenance=Provenance(source="SEC EDGAR", as_of=date(2025, 2, 1)),
    )
    assert fact.provenance.source == "SEC EDGAR"


def test_dossier_minimal_construction():
    d = CompanyDossier(
        ticker="NVDA",
        name="NVIDIA Corporation",
        generated_at=date(2026, 6, 6),
    )
    assert d.quote is None
    assert d.fundamentals is None
    assert d.filing_sections == []
    assert d.gaps == []


def test_dossier_with_quote_and_facts():
    d = CompanyDossier(
        ticker="NVDA",
        name="NVIDIA Corporation",
        quote=Quote(price=900.0, currency="USD", provenance=Provenance(source="yfinance")),
        fundamentals=Fundamentals(
            facts=[
                FinancialFact(concept="Revenues", value=60.0, provenance=Provenance(source="SEC EDGAR"))
            ]
        ),
        gaps=[SourceGap(source="FRED", reason="not configured")],
        generated_at=date(2026, 6, 6),
    )
    assert d.quote.price == 900.0
    assert d.fundamentals.facts[0].concept == "Revenues"
    assert d.gaps[0].source == "FRED"


def test_material_event_construction():
    from datetime import date as _date

    from saturn.models import MaterialEvent, Provenance

    ev = MaterialEvent(
        filing_date=_date(2026, 2, 21),
        item_codes=["2.02", "9.01"],
        title="Results of Operations and Financial Condition",
        excerpt="Q4 revenue was $X.",
        provenance=Provenance(source="SEC EDGAR"),
    )
    assert ev.form == "8-K"
    assert ev.item_codes == ["2.02", "9.01"]
    assert ev.full_text_cache_ref is None


def test_dossier_has_material_events_default():
    from datetime import date as _date

    from saturn.models import CompanyDossier

    d = CompanyDossier(ticker="NVDA", name="NVIDIA Corporation", generated_at=_date(2026, 6, 6))
    assert d.material_events == []


def test_derived_metric_and_input_models():
    from saturn.models import DerivedMetric, MetricInput, Provenance, CompanyDossier
    from datetime import date

    m = DerivedMetric(
        name="gross_margin",
        value=0.744,
        format="percent",
        fiscal_period="Q2 FY2026",
        formula="GrossProfit / Revenues",
        inputs=[MetricInput(concept="GrossProfit", fiscal_period="Q2 FY2026", value=17_755e6, source="SEC EDGAR")],
        provenance=Provenance(source="Saturn (derived)", as_of=date(2026, 6, 8)),
    )
    assert m.value == 0.744 and m.format == "percent"
    assert m.inputs[0].concept == "GrossProfit"

    d = CompanyDossier(ticker="X", name="X", generated_at=date(2026, 6, 8))
    assert d.derived_metrics == []          # default empty
    d.derived_metrics = [m]
    assert d.derived_metrics[0].name == "gross_margin"
