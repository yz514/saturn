"""Render a ResearchReport into markdown."""

from __future__ import annotations

from saturn.analytics.forward import is_reverse_dcf_low_confidence
from saturn.models import ResearchReport

_DISCLAIMER = (
    "*This report is for research and educational purposes only "
    "and is not investment advice.*"
)

# Per-concept caps for the human-facing financials table. The dossier keeps the
# full history; this only bounds what the markdown report renders for readability.
_RPT_MAX_ANNUAL = 3
_RPT_MAX_QUARTERS = 4
_RPT_MAX_CATALYSTS = 6

# Staleness gate: a concept is excluded from the table if its newest value is
# more than this many annual years / quarterly periods behind the dataset frontier.
_STALE_ANNUAL_YEARS = 1
_STALE_QUARTERS = 2


def _fmt_money(value: float | None) -> str:
    return f"${value:,.0f}" if value is not None else "N/A"


def _fmt_metric(value: float, fmt: str) -> str:
    if fmt == "percent":
        return f"{value * 100:.1f}%"
    if fmt == "x":
        return f"{value:.1f}x"
    if fmt == "currency":
        return _fmt_money(value)
    if fmt == "per_share":
        return f"${value:,.2f}"
    return f"{value:.2f}"   # ratio


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


_RPT_MAX_METRIC_ANNUAL = 2
_RPT_MAX_METRIC_QUARTERS = 1


def _select_report_metrics(metrics: list) -> list:
    by_name: dict[str, list] = {}
    order: list[str] = []
    for m in metrics:
        if m.name not in by_name:
            by_name[m.name] = []
            order.append(m.name)
        by_name[m.name].append(m)
    out: list = []
    for name in order:
        items = by_name[name]
        annual = [m for m in items if (m.fiscal_period or "").startswith("FY")]
        quarterly = [m for m in items if (m.fiscal_period or "").startswith("Q")]
        other = [m for m in items if not (m.fiscal_period or "").startswith(("FY", "Q"))]
        annual.sort(key=lambda m: _annual_sort_key(m.fiscal_period), reverse=True)
        quarterly.sort(key=lambda m: _quarter_sort_key(m.fiscal_period), reverse=True)
        out += annual[:_RPT_MAX_METRIC_ANNUAL] + quarterly[:_RPT_MAX_METRIC_QUARTERS] + other
    return out


def _q_ord(period: str) -> int | None:
    fy, q = _quarter_sort_key(period)
    return fy * 4 + q if fy > 0 else None


def _select_report_facts(facts: list) -> tuple[list, list[tuple[str, str]]]:
    """Per concept, keep the most-recent annual/quarterly periods for the table, but
    EXCLUDE a concept entirely when it has no recent value (e.g. a tag migration we
    don't map); excluded concepts are returned as (concept, latest_period) warnings."""
    by_concept: dict[str, list] = {}
    order: list[str] = []
    for f in facts:
        if f.concept not in by_concept:
            by_concept[f.concept] = []
            order.append(f.concept)
        by_concept[f.concept].append(f)

    annual_years = [_annual_sort_key(f.fiscal_period) for f in facts
                    if not (f.fiscal_period or "").startswith("Q") and _annual_sort_key(f.fiscal_period) > 0]
    q_ords = [o for o in (_q_ord(f.fiscal_period) for f in facts if (f.fiscal_period or "").startswith("Q")) if o is not None]
    newest_fy = max(annual_years) if annual_years else None
    newest_q = max(q_ords) if q_ords else None

    annual_out: list = []
    quarterly_out: list = []
    warnings: list[tuple[str, str]] = []
    for concept in order:
        items = by_concept[concept]
        annual = sorted([x for x in items if not (x.fiscal_period or "").startswith("Q")],
                        key=lambda x: _annual_sort_key(x.fiscal_period), reverse=True)
        quarterly = sorted([x for x in items if (x.fiscal_period or "").startswith("Q")],
                           key=lambda x: _quarter_sort_key(x.fiscal_period), reverse=True)
        annual_fresh = newest_fy is None or (bool(annual) and _annual_sort_key(annual[0].fiscal_period) >= newest_fy - _STALE_ANNUAL_YEARS)
        quarterly_fresh = newest_q is None or (bool(quarterly) and (_q_ord(quarterly[0].fiscal_period) or -1) >= newest_q - _STALE_QUARTERS)
        kept_annual = annual[:_RPT_MAX_ANNUAL] if annual_fresh else []
        kept_quarterly = quarterly[:_RPT_MAX_QUARTERS] if quarterly_fresh else []
        if not kept_annual and not kept_quarterly and items:
            latest = annual[0].fiscal_period if annual else (quarterly[0].fiscal_period if quarterly else "N/A")
            warnings.append((concept, latest))
            continue
        annual_out.extend(kept_annual)
        quarterly_out.extend(kept_quarterly)
    return annual_out + quarterly_out, warnings


def _render_catalysts_from_events(events: list) -> list[str]:
    """Compact catalyst lines from recent 8-K material events, used for §8 when no
    third-party news feed is available. Full excerpts stay in §17 (no duplication)."""
    lines: list[str] = []
    for ev in sorted(events, key=lambda e: e.filing_date, reverse=True)[:_RPT_MAX_CATALYSTS]:
        head = f"- **{ev.filing_date}** — {ev.title or ev.form}"
        if ev.item_codes:
            head += f" (items {', '.join(ev.item_codes)})"
        if ev.provenance.source_url:
            head += f" — [filing]({ev.provenance.source_url})"
        lines.append(head)
    if lines:
        lines.append("")
        lines.append("_Recent catalysts from SEC 8-K filings; no third-party news feed available._")
    return lines


def _render_alpha(thesis) -> list[str]:
    suffix = " (Incomplete — low confidence)" if thesis.incompleteness else ""
    out: list[str] = [f"## 2. Alpha Thesis{suffix}", ""]  # §2 — also update render() else-branch if order changes
    a = thesis.anchor
    out.append(f"**Anchor** ({a.source}): {a.text}")
    out.append("")
    out.append(f"**Stance:** {thesis.stance.replace('_', ' ')} · confidence {thesis.confidence}")
    out.append("")
    if thesis.variant:
        out += [f"**Variant perception:** {thesis.variant}", ""]
    if thesis.rationale:
        out += [f"**Rationale:** {thesis.rationale}", ""]
    out.append(f"**Key variable:** {thesis.key_variable or 'N/A'}")
    out.append(f"**Falsifier:** {thesis.falsifier or 'N/A'}")
    out.append(f"**Horizon:** {thesis.horizon or 'N/A'}")
    out.append("")
    if thesis.scenarios:
        out.append("| Scenario | Period | Driver | Math | Price | Return |")
        out.append("| --- | --- | --- | --- | --- | --- |")
        for s in thesis.scenarios:
            math = f"{s.per_share_value:g} {s.metric} × {s.multiple:g} {s.multiple_basis}"
            price = f"${s.implied_price:,.2f}" if s.implied_price is not None else "N/A"
            ret = f"{s.implied_return_pct:+.0%}" if s.implied_return_pct is not None else "N/A"
            driver = s.driver.replace("|", "\\|")   # free-text; keep it inside one table cell
            out.append(f"| {s.name} | {s.period} | {driver} | {math} | {price} | {ret} |")
        out.append("")
    if thesis.incompleteness:
        out += [f"_Alpha thesis incomplete: {', '.join(thesis.incompleteness)}._", ""]
    return out


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
    if report.alpha_thesis is not None:
        out += _render_alpha(report.alpha_thesis)
    else:
        out += ["## 2. Alpha Thesis", "", "_Alpha thesis unavailable this run._", ""]
    out += ["## 3. Company Overview", "", a.company_overview, ""]
    out += ["## 4. Business Segments", "", a.business_segments, ""]

    ic = c.industry_context
    if ic and ic.peers:
        out += ["### Value-Chain / Demand Context", ""]
        out.append("| Peer | Role | Rev growth YoY | CapEx | CapEx/Rev |")
        out.append("| --- | --- | --- | --- | --- |")
        for p in ic.peers:
            rg = f"{p.revenue_growth_yoy:+.1%}" if p.revenue_growth_yoy is not None else "N/A"
            cx = f"${p.capex / 1e9:.1f}B" if p.capex is not None else "N/A"
            ci = f"{p.capex_intensity:.1%}" if p.capex_intensity is not None else "N/A"
            out.append(f"| {p.ticker} | {p.role} | {rg} | {cx} | {ci} |")
        out += ["", f"_{ic.note}_", ""]

    out += ["## 5. Recent Market Performance", ""]
    if c.quote:
        out.append(f"- Price: {_fmt_money(c.quote.price)} {c.quote.currency or ''}".rstrip())
        out.append(f"- Market cap: {_fmt_money(c.quote.market_cap)}")
        out.append(f"- _Source: {c.quote.provenance.source}_")
    else:
        out.append("_No quote available._")
    out.append("")

    out += ["## 6. Financial Snapshot", ""]
    if c.fundamentals and c.fundamentals.facts:
        out.append("| Concept | Period | Value | Unit | Source |")
        out.append("| --- | --- | --- | --- | --- |")
        selected, stale_warnings = _select_report_facts(c.fundamentals.facts)
        for fact in selected:
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
        if stale_warnings:
            out.append("")
            out.append("_Data Quality Warnings — excluded from the table (no recent value; likely an unmapped XBRL tag):_")
            for concept, latest in stale_warnings:
                out.append(f"- {concept} — latest available {latest}")
        out.append("")
    out += [a.financial_snapshot, ""]

    out += ["## 7. Key Metrics", ""]
    _derived = [m for m in c.derived_metrics if m.provenance.source != "Saturn (model)"]
    _forward = [m for m in c.derived_metrics if m.provenance.source == "Saturn (model)"]
    if _derived:
        out.append("| Metric | Period | Value | Formula |")
        out.append("| --- | --- | --- | --- |")
        for m in _select_report_metrics(_derived):
            out.append(
                f"| {m.name} | {m.fiscal_period or 'current'} | "
                f"{_fmt_metric(m.value, m.format)} | {m.formula} |"
            )
        out.append("")
        out.append(
            f"_Showing the most recent {_RPT_MAX_METRIC_ANNUAL} annual and "
            f"{_RPT_MAX_METRIC_QUARTERS} quarterly periods per metric. "
            "Definitions & formulas: docs/metrics.md_"
        )
    else:
        out.append("_No derived metrics available._")
    out.append("")
    if _forward:
        out += ["### Forward / Expectations (model estimate)", ""]
        out.append("| Metric | Value | Formula |")
        out.append("| --- | --- | --- |")
        for m in _forward:
            out.append(f"| {m.name} | {_fmt_metric(m.value, m.format)} | {m.formula} |")
        out.append("")
        out.append(
            "_Reverse-DCF model (10-yr horizon, 2.5% terminal growth, 8/10/12% discount). "
            "Model estimate from price + as-reported FCF, not as-reported. See docs/metrics.md._"
        )
        if is_reverse_dcf_low_confidence(_forward):
            out.append("")
            out.append(
                "_⚠️ **Low confidence:** the price implies FCF growth beyond the model's bounds, so the "
                "trailing FCF base is likely cycle-depressed. Treat fair value / margin of safety as a "
                "diagnostic, not a primary valuation._"
            )
        out.append("")

    out += ["### Consensus / Analyst Expectations (estimate)", ""]
    cons = c.consensus
    _has = cons and any(v is not None for v in (
        cons.forward_pe, cons.target_mean, cons.rating, cons.last_eps_surprise_pct))
    if _has:
        out.append("| Field | Value |")
        out.append("| --- | --- |")
        if cons.forward_pe is not None:
            out.append(f"| Forward P/E | {cons.forward_pe:.1f}x |")
        if cons.peg is not None:
            out.append(f"| PEG | {cons.peg:.2f} |")
        if cons.target_mean is not None:
            rng = f" (range {_fmt_money(cons.target_low)}–{_fmt_money(cons.target_high)})" if cons.target_low is not None else ""
            up = f", {cons.target_upside_pct * 100:+.1f}% vs price" if cons.target_upside_pct is not None else ""
            out.append(f"| Price target (mean) | {_fmt_money(cons.target_mean)}{rng}{up} |")
        if cons.rating is not None:
            out.append(f"| Analyst rating | {cons.rating} ({cons.n_analysts} analysts) |")
        if cons.last_eps_surprise_pct is not None:
            out.append(f"| Last EPS surprise | {cons.last_eps_surprise_pct * 100:+.1f}% |")
        out.append("")
        out.append(
            "_Best-effort analyst estimates from yfinance; values failing validation "
            "against as-reported data are dropped. Not as-reported._"
        )
        if cons.rejected:
            out.append("")
            out += [f"- rejected — {r}" for r in cons.rejected]
    elif cons is not None and cons.rejected:
        out.append("_No consensus values passed validation; all were rejected:_")
        out += [f"- rejected — {r}" for r in cons.rejected]
    else:
        out.append("_No analyst consensus available._")
    out.append("")

    out += ["## 8. Recent News and Catalysts", ""]
    if c.news:
        for item in c.news:
            suffix = f" — {item.publisher}" if item.publisher else ""
            if item.link:
                out.append(f"- [{item.title}]({item.link}){suffix}")
            else:
                out.append(f"- {item.title}{suffix}")
    elif c.material_events:
        out += _render_catalysts_from_events(c.material_events)
    else:
        out.append("_No recent news available._")
    out.append("")

    out += ["## 9. Bull Thesis", "", d.bull_thesis, ""]
    out += ["## 10. Bear Thesis", "", d.bear_thesis, ""]
    out += ["## 11. Key Risks", "", a.key_risks, ""]
    out += ["## 12. Valuation Discussion", "", a.valuation_discussion, ""]
    out += ["## 13. Open Questions", "", a.open_questions, ""]
    out += ["## 14. Final View", "", d.final_view, ""]

    out += ["## 15. Verification (Critic)", ""]
    cr = report.critic_review
    if cr is None:
        out.append("_Verification unavailable._")
    elif not cr.findings:
        if cr.repaired:
            out.append(f"_Auto-corrected by the Critic (self-repair): all flagged issues resolved ({cr.claims_checked} claims checked)._")
        else:
            out.append(f"_No material discrepancies found against the underlying data ({cr.claims_checked} claims checked)._")
    else:
        if cr.repaired:
            out.append("_Auto-corrected by the Critic (self-repair); the issue(s) below remain._")
            out.append("")
        out.append(f"{cr.summary} ({cr.claims_checked} claims checked)")
        out.append("")
        for f in cr.findings:
            out.append(f"- ⚠️ **{f.category}** [{f.section}, {f.severity}]: \"{f.claim}\" — {f.evidence}")
    out.append("")

    out += ["## 16. Macro Snapshot", ""]
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

    out += ["## 17. Material Events (SEC 8-K)", ""]
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

    out += ["## 18. Sources", ""]
    if report.sources:
        out += [f"- {s}" for s in report.sources]
    else:
        out.append("_No sources recorded._")
    out.append("")

    if c.gaps:
        out += ["## 19. Data Gaps", ""]
        out += [f"- **{g.source}**: {g.reason}" for g in c.gaps]
        out.append("")

    out += ["---", "", _DISCLAIMER, ""]
    return "\n".join(out)
