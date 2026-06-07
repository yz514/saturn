"""Render a ResearchReport into markdown."""

from __future__ import annotations

from saturn.models import ResearchReport

_DISCLAIMER = (
    "*This report is for research and educational purposes only "
    "and is not investment advice.*"
)

# Per-concept caps for the human-facing financials table. The dossier keeps the
# full history; this only bounds what the markdown report renders for readability.
_RPT_MAX_ANNUAL = 3
_RPT_MAX_QUARTERS = 4


def _fmt_money(value: float | None) -> str:
    return f"${value:,.0f}" if value is not None else "N/A"


def _annual_sort_key(period: str) -> int:
    """'FY2024' -> 2024; non-FY periods sort last."""
    try:
        return int((period or "").replace("FY", "").strip())
    except (ValueError, AttributeError):
        return -1


def _quarter_sort_key(period: str) -> tuple[int, int]:
    """'Q2 FY2025' -> (2025, 2); unparseable sorts last."""
    try:
        q_part, fy_part = period.split()
        return (int(fy_part.replace("FY", "")), int(q_part[1]))
    except (ValueError, AttributeError, IndexError):
        return (-1, -1)


def _select_report_facts(facts: list) -> list:
    """Per concept, keep the most-recent annual and quarterly periods for the
    table, preserving the dossier's concept ordering (annual block then quarterly)."""
    by_concept: dict[str, list] = {}
    order: list[str] = []
    for f in facts:
        if f.concept not in by_concept:
            by_concept[f.concept] = []
            order.append(f.concept)
        by_concept[f.concept].append(f)
    annual_out: list = []
    quarterly_out: list = []
    for concept in order:
        items = by_concept[concept]
        annual = [x for x in items if not (x.fiscal_period or "").startswith("Q")]
        quarterly = [x for x in items if (x.fiscal_period or "").startswith("Q")]
        annual.sort(key=lambda x: _annual_sort_key(x.fiscal_period), reverse=True)
        quarterly.sort(key=lambda x: _quarter_sort_key(x.fiscal_period), reverse=True)
        annual_out.extend(annual[:_RPT_MAX_ANNUAL])
        quarterly_out.extend(quarterly[:_RPT_MAX_QUARTERS])
    return annual_out + quarterly_out


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
        for fact in _select_report_facts(c.fundamentals.facts):
            val = _fmt_money(fact.value) if (fact.unit or "").upper() == "USD" else (
                fact.value if fact.value is not None else "N/A"
            )
            out.append(
                f"| {fact.concept} | {fact.fiscal_period or 'N/A'} | {val} "
                f"| {fact.unit or ''} | {fact.provenance.source} |"
            )
        out.append("")
        out.append(
            f"_Showing the most recent {_RPT_MAX_ANNUAL} annual and "
            f"{_RPT_MAX_QUARTERS} quarterly periods per concept._"
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
