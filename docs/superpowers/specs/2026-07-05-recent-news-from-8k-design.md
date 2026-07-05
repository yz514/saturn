# Recent News from 8-K — Design

**Date:** 2026-07-05
**Status:** Approved → ready for plan
**Author:** Saturn (Claude) + user

## 1. Goal

Stop §7 "Recent News and Catalysts" from reading "_No recent news available._" when
the report clearly has recent catalysts. External review of MU_2026-07-05 flagged this:
§7 was empty while §15 Material Events listed the Q3 FY2026 earnings 8-K, senior-notes
tender offers, and a director appointment — the actual recent catalysts.

## 2. Root cause

§7 renders only from `dossier.news` (yfinance `NewsItem`s), which is empty for most
names (yfinance news is unreliable — the F2 gap). The rich 8-K `MaterialEvent`s we
already ingest and render in §15 are never used to fill §7.

## 3. Design (render-only)

Change the §7 branch in `saturn/reports/markdown_report.py` to a three-way fallback:

1. `c.news` present → render yfinance news as today (unchanged).
2. else `c.material_events` present → render the most-recent 8-Ks as dated catalysts.
3. else → "_No recent news available._" (unchanged).

**Catalyst line format** (compact — the full excerpt stays in §15, no duplication):
```
- **2026-06-24** — <title> (items 2.02, 9.01) — [filing](<url>)
```
Sorted by `filing_date` descending, capped at the most recent `_RPT_MAX_CATALYSTS = 6`.
Followed by a source note: `_Recent catalysts from SEC 8-K filings; no third-party news feed available._`

A small helper `_render_catalysts_from_events(events) -> list[str]` keeps `render` tidy.

## 4. Scope

- **Modify:** `saturn/reports/markdown_report.py` (§7 branch + helper + `_RPT_MAX_CATALYSTS`).
- **Test:** `tests/reports/test_markdown_report.py` (events fill §7 when news empty;
  yfinance news still wins when present; neither → "no news" line).
- No change to models, dossier, ingestion, LLM context, or §15.

## 5. Edge cases

- Event with no `title` → show `8-K` + item codes. No `item_codes` → omit the `(items …)`
  clause. No `source_url` → omit the `[filing]` link. (Mirror §15's defensive rendering.)
- More than 6 events → cap at 6 most recent (§15 still lists all).

## 6. Out of scope

- Materiality (High/Med/Low) classification — that's judgment (Critic territory).
- Merging news + events, or de-duplicating across the two — fallback-when-empty only.
- The Open-Questions ↔ Material-Events contradiction (#5) — that's the Critic.
