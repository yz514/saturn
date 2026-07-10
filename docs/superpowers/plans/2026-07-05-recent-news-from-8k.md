# Recent News from 8-K — Implementation Plan

> **For agentic workers:** Small render-only slice; execute inline with TDD.

**Goal:** §7 falls back to the recent 8-K Material Events when yfinance news is empty.

**Design:** `docs/superpowers/specs/2026-07-05-recent-news-from-8k-design.md`

---

### Task 1: §7 falls back to Material Events

**Files:**
- Modify: `saturn/reports/markdown_report.py` (§7 branch, helper, `_RPT_MAX_CATALYSTS`)
- Test: `tests/reports/test_markdown_report.py`

- [ ] **Step 1: Write failing tests**

Build a `ResearchReport` whose dossier has empty `news` but non-empty `material_events`;
assert §7 lists the event date/title and NOT the "No recent news" line; a second test with
yfinance `news` present asserts the news still renders (events not used); a third with
neither asserts the "No recent news available." line. Use the existing report-construction
helper in the test module (follow the pattern already used for §15 / material_events tests).

- [ ] **Step 2: Run to verify they fail.**

- [ ] **Step 3: Implement.** Add `_RPT_MAX_CATALYSTS = 6`, a helper:
```python
def _render_catalysts_from_events(events: list) -> list[str]:
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
```
Change the §7 branch to: `if c.news: <existing> elif c.material_events: out += _render_catalysts_from_events(c.material_events) else: out.append("_No recent news available._")`.

- [ ] **Step 4: Run report tests + full suite — all green.**

- [ ] **Step 5: Commit.**

## Verification
Regenerate the MU report; confirm §7 now lists the 2026-06-24 Q3 earnings 8-K, the
March tender-offer 8-Ks, and the director-appointment 8-K instead of "No recent news."
