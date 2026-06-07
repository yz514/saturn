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
        out.append(f"- _Source: {c.quote.provenance.source}_")
    else:
        out.append("_No quote available._")
    out.append("")

    out += ["## 5. Financial Snapshot", ""]
    if c.fundamentals and c.fundamentals.facts:
        out.append("| Concept | Period | Value | Unit | Source |")
        out.append("| --- | --- | --- | --- | --- |")
        _annual = [x for x in c.fundamentals.facts if not (x.fiscal_period or "").startswith("Q")]
        _quarterly = [x for x in c.fundamentals.facts if (x.fiscal_period or "").startswith("Q")]
        for fact in _annual + _quarterly:
            val = _fmt_money(fact.value) if (fact.unit or "").upper() == "USD" else (
                fact.value if fact.value is not None else "N/A"
            )
            out.append(
                f"| {fact.concept} | {fact.fiscal_period or 'N/A'} | {val} "
                f"| {fact.unit or ''} | {fact.provenance.source} |"
            )
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

    out += ["## 13. Macro Snapshot", ""]
    if c.macro and c.macro.series:
        out.append("| Series | Latest | As of | Source |")
        out.append("| --- | --- | --- | --- |")
        for m in c.macro.series:
            latest = m.observations[-1] if m.observations else None
            val = latest[1] if latest else "N/A"
            asof = latest[0] if latest else "N/A"
            out.append(f"| {m.title} | {val} | {asof} | {m.provenance.source} |")
        out.append("")
    else:
        out.append("_No macro data available._")
        out.append("")

    out += ["## 14. Material Events (SEC 8-K)", ""]
    if c.material_events:
        for ev in c.material_events:
            labels = ", ".join(ev.item_codes)
            head = f"- **{ev.filing_date}**"
            if ev.title:
                head += f" — {ev.title}"
            if labels:
                head += f" (items {labels})"
            if ev.provenance.source_url:
                head += f" — [filing]({ev.provenance.source_url})"
            out.append(head)
            if ev.excerpt:
                quoted = "\n  > ".join(ev.excerpt.splitlines() or [ev.excerpt])
                out.append(f"  > {quoted}")
        out.append("")
    else:
        out.append("_No material events in the last 12 months._")
        out.append("")

    out += ["## 15. Sources", ""]
    if report.sources:
        out += [f"- {s}" for s in report.sources]
    else:
        out.append("_No sources recorded._")
    out.append("")

    if c.gaps:
        out += ["## 16. Data Gaps", ""]
        out += [f"- **{g.source}**: {g.reason}" for g in c.gaps]
        out.append("")

    out += ["---", "", _DISCLAIMER, ""]
    return "\n".join(out)
