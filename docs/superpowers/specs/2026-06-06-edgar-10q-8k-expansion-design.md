# EDGAR Coverage Expansion — 10-Q Quarterly + 8-K Events — Design Spec

**Date:** 2026-06-06
**Status:** Approved (design sign-off); pending spec review → `writing-plans`.
**Author:** Saturn dev workflow (brainstorming skill).
**Builds on:** the EDGAR adapter merged in PR #5 (`saturn/ingestion/edgar.py`, `identifiers.py`, `http.py`). Branch off `main` (PR #4 + #5 merged).

## Motivation

The shipped EDGAR adapter surfaces **annual** as-reported fundamentals (10-K) plus targeted 10-K text sections. Two high-value gaps remain for real value-investing research:

1. **Quarterly trajectory** — 10-Q numbers (QoQ / TTM trends, latest-quarter results). Crucially, the `companyfacts` payload we already fetch *contains* quarterly figures; the current parser filters them out. Adding them is almost free.
2. **Material events** — 8-K filings are the primary-source "news" of a company (earnings releases, guidance, M&A, executive changes, material agreements). They are higher-signal and authoritative compared to yfinance's off-topic headlines, so they also address follow-up **F2 (news relevance)**.

This slice adds both, plus the **10-Q MD&A** narrative (management's explanation of the quarter), reusing the existing canonical-model + provenance + dispatcher + cache + typed-error machinery.

## Scope

**In scope**
- Quarterly `FinancialFact`s (last ~8 quarters) from `companyfacts`, merged into `Fundamentals.facts`.
- Latest **10-Q MD&A** section as a `FilingSection` (alongside the 10-K sections).
- **8-K material events** over the last ~12 months: an event index (filing date, item codes + human labels, title, link) for every 8-K, plus a bounded **body excerpt** for high-value items.
- New canonical `MaterialEvent` type + `CompanyDossier.material_events` field.
- Report + agent-context rendering of quarterly facts and material events (provenance-tagged).
- A focused file split: move EDGAR *document* handling into `saturn/ingestion/edgar_filings.py`.

**Out of scope (deferred)**
- Other filing types (DEF 14A proxy, Form 4 insider, 13D/G) — future slices.
- Per-call caching of `companyfacts`/`submissions` (cache module ready; still deferred).
- Sector/industry/business-summary identity (profile source / FMP slice).
- LLM summarization of any filing text (ingestion stays deterministic).
- 8-K full-document RAG (excerpt + cached full text only; retrieval is Phase 3).

## §1. Canonical model changes

- **Quarterly financials reuse `FinancialFact`** — no model change. Annual rows use `fiscal_period="FY2024"`; quarterly rows use `fiscal_period="Q3 FY2024"`. Both live in `Fundamentals.facts`.
- **New `MaterialEvent`** (in `saturn/models.py`):
  ```python
  class MaterialEvent(BaseModel):
      form: str = "8-K"
      filing_date: date
      item_codes: list[str] = Field(default_factory=list)   # e.g. ["2.02", "9.01"]
      title: str | None = None                              # primary item label / doc title
      excerpt: str | None = None                            # bounded body text, only for high-value items
      full_text_cache_ref: str | None = None
      provenance: Provenance
  ```
- **`CompanyDossier` gains** `material_events: list[MaterialEvent] = Field(default_factory=list)`.
- Adapter-level constants (in `edgar_filings.py`, not the model): `EIGHT_K_ITEM_LABELS: dict[str, str]` (code → human label, for rendering) and `HIGH_VALUE_8K_ITEMS = {"1.01", "2.01", "2.02", "5.02", "7.01", "8.01"}` (which item codes trigger body-excerpt extraction).

## §2. Code structure (file split)

- **New `saturn/ingestion/edgar_filings.py`** — owns EDGAR *document* handling:
  - Moved from `edgar.py`: `_strip_html`, `_section_between`, `_extract_filing_sections`, `_SECTION_SPECS`, and the URL constants for archives/submissions as needed.
  - Generalized: `_select_latest_10k(submissions)` → `_select_latest(submissions, form)` (returns the most recent filing of a given form, same dict shape).
  - New: `_select_recent_8ks(submissions, *, since: date)` → list of 8-K entries (accession, primary_document, filing_date, item codes) filed on/after `since`; `_extract_8k(html) -> str` (best-effort body text, reusing `_strip_html`); `_parse_8k_items(submissions_entry) -> list[str]` (item codes from the submissions feed's `items` field).
  - Constants: `EIGHT_K_ITEM_LABELS`, `HIGH_VALUE_8K_ITEMS`.
- **`edgar.py` keeps**: identifiers wiring, `companyfacts`/`_parse_companyfacts` (extended for quarterly), the thin fetchers (`_fetch_companyfacts`/`_fetch_submissions`/`_fetch_filing_html`/`_ua`/`_cache_full_text`), and `fetch_edgar` orchestration (now importing from `edgar_filings`).
- Rationale: keeps both files focused (~250 lines each) instead of growing `edgar.py` toward ~450; document/section/event logic is one cohesive responsibility.

## §3. Parsing & fetch logic

- **`_parse_companyfacts(raw, *, max_years=4, max_quarters=8)`** — unchanged annual logic, plus: for each concept, also collect entries where `fp in {"Q1","Q2","Q3","Q4"}` and `form` starts with `"10-Q"`; keep the latest-filed per (fy, quarter); take the most recent `max_quarters`; emit `FinancialFact(fiscal_period=f"{fp} FY{fy}", unit="USD", provenance=...)`. Annual and quarterly facts share `Fundamentals.facts`.
- **`fetch_edgar`** additionally:
  1. `sub = _fetch_submissions(cik)` (already fetched once; reuse it).
  2. **10-Q MD&A:** `q10 = _select_latest(sub, "10-Q")`; if present, fetch its doc and run `_extract_filing_sections`, appending the resulting sections (esp. MD&A) as `FilingSection`s with the 10-Q's `filing_date` as provenance `as_of`. (Annual + quarterly MD&A coexist, distinguished by `as_of`.)
  3. **8-K events:** `events = _select_recent_8ks(sub, since=today - ~365d)`. For each, build a `MaterialEvent` (item codes + label-derived title + provenance). If any code ∈ `HIGH_VALUE_8K_ITEMS`, fetch the 8-K doc, `_extract_8k` it, set a bounded `excerpt` (≤ `_EXCERPT_CHARS`) and cache the full text (`_cache_full_text`).
- **`fetch_edgar` return** dict gains `"material_events": list[MaterialEvent]`; quarterly facts ride inside `"fundamentals"`. Keys: `{fundamentals, filing_sections, material_events, name, cik}`.

## §4. Integration & rendering

- **`build_dossier`** — extend the edgar-result unpacking to also pull `material_events = edgar_result.get("material_events")` and pass it into `CompanyDossier(material_events=... or [])`. Same defensive `isinstance(edgar_result, dict)` guard; the existing soft-fail gap behavior is unchanged.
- **Report renderer (`markdown_report.py`)**:
  - Financials table groups **annual rows first, then quarterly** (so `FY2024` and `Q3 FY2024` don't interleave confusingly); a small sort key on `fiscal_period`.
  - New **"Material Events (SEC 8-K)"** section: a table/list of `filing_date · item labels · title · source link`, with the excerpt rendered beneath high-value entries. Renders `_No material events in the last 12 months._` when empty.
- **`_company_context` (workflow)** — render quarterly facts (already covered by the fundamentals loop) and a `MATERIAL EVENTS` block (date, item labels, excerpt) with inline provenance, so the LLM and a future Critic can cite primary-source events. This is the F1/F2 payoff.

## §5. Error handling & caching

- 8-K/10-Q document fetches reuse `http_get` + `_ua()` (requires `SEC_USER_AGENT`); any transport failure → `SourceFailure` → recorded as the single `edgar` gap (no crash). A missing `SEC_USER_AGENT` already degrades the whole EDGAR source to a gap.
- **Genuine absence ≠ failure:** no 8-Ks in the window → `material_events == []` (not a gap); no 10-Q → no quarterly MD&A section (not a gap).
- Per-event/per-section full text cached under `edgar_sections` (existing helper). `companyfacts`/`submissions` per-call caching remains deferred.
- 8-K body extraction is **best-effort** (same regex/`_strip_html` approach as 10-K), excerpt-bounded; if extraction yields nothing, the event still appears in the index with no excerpt.

## §6. Testing (offline)

All network seams monkeypatched/injected; pure parsers tested against committed fixtures.
- Extend `tests/fixtures/edgar/companyfacts_NVDA.json` with quarterly (10-Q, fp=Q1–Q4) rows.
- Extend/add a `submissions` fixture containing recent 8-Ks (with `items` field) and a 10-Q.
- Add `tests/fixtures/edgar/eightk_excerpt.html` (multi-item 8-K including a 2.02 body).
- Cases: quarterly extraction + latest-per-quarter + `max_quarters` cap; `_select_latest(form)`; `_select_recent_8ks` window filtering; `_parse_8k_items`; `_extract_8k` body; high-value-item triggers excerpt while others stay index-only; `fetch_edgar` assembles `material_events` (fetchers monkeypatched); renderer shows the 8-K section; `_company_context` includes the events block. Suite stays fully offline.

## Success criteria

- `saturn research <TICKER>` (live, with `SEC_USER_AGENT`) produces a report whose financials show **annual + recent quarterly** as-reported figures, a **10-Q MD&A** section, and a **Material Events (SEC 8-K)** section over the last ~12 months — each datum provenance-tagged.
- `--mock` and the full suite still run fully offline; the mock dossier gains a sample quarterly fact + a sample `MaterialEvent`.
- Missing/blocked sources still degrade to recorded gaps; empty windows render as "none," not gaps.
- `edgar.py` and `edgar_filings.py` each remain focused and independently testable.

## Next step

Spec self-review → user review → invoke `writing-plans` to decompose into bite-sized TDD tasks (model → file split/move → quarterly parse → `_select_latest`/8-K selectors → `_extract_8k` → `fetch_edgar` wiring → dossier/report/context integration → mock fixture → verification).
