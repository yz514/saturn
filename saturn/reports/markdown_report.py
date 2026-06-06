"""Render a ResearchReport into markdown."""

from __future__ import annotations

from saturn.models import ResearchReport

_DISCLAIMER = (
    "*This report is for research and educational purposes only "
    "and is not investment advice.*"
)


def _fmt_money(value: float | None) -> str:
    return f"${value:,.0f}" if value is not None else "N/A"


def render(report: ResearchReport) -> str:
    """Return the markdown for a research report (pure function)."""
    c = report.company
    a = report.analysis
    d = report.debate
    out: list[str] = []

    out.append(f"# {report.ticker} Equity Research Report")
    out.append("")
    meta = f"*Generated {report.generated_at:%Y-%m-%d} · model: {report.model_used}"
    if report.mock:
        meta += " · MOCK DATA"
    meta += "*"
    out.append(meta)
    out.append("")

    out += ["## 1. Executive Summary", "", a.executive_summary, ""]
    out += ["## 2. Company Overview", "", a.company_overview, ""]
    out += ["## 3. Business Segments", "", a.business_segments, ""]

    out += ["## 4. Recent Market Performance", ""]
    if c.quote:
        out.append(f"- Price: {_fmt_money(c.quote.price)} {c.quote.currency or ''}".rstrip())
        out.append(f"- Market cap: {_fmt_money(c.quote.market_cap)}")
    else:
        out.append("- Price: N/A")
        out.append("- Market cap: N/A")
    out.append("")

    out += ["## 5. Financial Snapshot", ""]
    if c.fundamentals and c.fundamentals.facts:
        out.append("| Concept | Period | Value |")
        out.append("| --- | --- | --- |")
        for fact in c.fundamentals.facts:
            val = f"{fact.value:,.0f} {fact.unit or ''}".strip() if fact.value is not None else "N/A"
            out.append(f"| {fact.concept} | {fact.fiscal_period or '?'} | {val} |")
        out.append("")
    out += [a.financial_snapshot, ""]

    out += ["## 6. Recent News and Catalysts", ""]
    if c.news:
        for item in c.news:
            suffix = f" — {item.publisher}" if item.publisher else ""
            if item.link:
                out.append(f"- [{item.title}]({item.link}){suffix}")
            else:
                out.append(f"- {item.title}{suffix}")
    else:
        out.append("_No recent news available._")
    out.append("")

    out += ["## 7. Bull Thesis", "", d.bull_thesis, ""]
    out += ["## 8. Bear Thesis", "", d.bear_thesis, ""]
    out += ["## 9. Key Risks", "", a.key_risks, ""]
    out += ["## 10. Valuation Discussion", "", a.valuation_discussion, ""]
    out += ["## 11. Open Questions", "", a.open_questions, ""]
    out += ["## 12. Final View", "", d.final_view, ""]

    out += ["## 13. Sources", ""]
    if report.sources:
        out += [f"- {s}" for s in report.sources]
    else:
        out.append("_No sources recorded._")
    out.append("")

    out += ["---", "", _DISCLAIMER, ""]
    return "\n".join(out)
