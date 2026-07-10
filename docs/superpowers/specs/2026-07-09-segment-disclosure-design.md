# Segment Disclosure (Option 1) — Design

**Date:** 2026-07-09
**Status:** Approved (brainstorm) → ready for plan
**Author:** Saturn (Claude) + user

## 1. Goal

Stop §3 "Business Segments" from saying "no granular segment data available" when the
company publishes a business-unit / segment table in its earnings release. Feed that
table's **text** into the analyst context so §3 can render a real segment table with
usable numbers (e.g. MU Cloud Memory $13.769B / 83% GM / 78% OpM). Flagged by two
external reviews.

## 2. Why "feed text to the analyst" (not structured extraction)

Segment data is **not in the SEC `companyfacts` API** — it is dimensionless (a revenue
row is `{start,end,val,fy,fp,form,filed,frame}`, no segment axis/member). The per-BU
numbers live only in filing text (the earnings-release exhibit) or the raw dimensioned
XBRL instance. Rather than an LLM extractor at the data layer (hallucination risk,
against our F1 grain) or a heavy raw-XBRL-dimension parser, we ingest the relevant
**press-release text** as a `FilingSection`; the analyst reads it and renders the table,
and the Critic (next project) verifies the transcribed numbers against the source.

Confirmed feasible: MU's earnings 8-K accession `index.json` lists
`a2026q3ex991-pressrelease.htm`, which contains the full standalone-quarterly BU table
(and, as a bonus, Q3 adjusted FCF and Q4 guidance).

## 3. Sourcing

1. Among the recent 8-Ks we already list (`_select_recent_8ks`), pick the newest
   **earnings 8-K** = one whose `item_codes` contains `"2.02"` (Results of Operations).
2. Fetch that accession's `index.json`
   (`https://www.sec.gov/Archives/edgar/data/{cik}/{accn_nodash}/index.json`) and find
   the **exhibit-99 press release**: a `directory.item[].name` ending in `.htm`/`.html`
   and containing `99` (prefer names also containing `press`/`ex99`/`991`).
3. Fetch it, `_strip_html` → text, then extract the **segment region** (anchored on
   `Business Unit`/`Segment`) with leading context, capped at `_SEGMENT_MAX_CHARS = 6000`.

## 4. Integration (minimal — reuse existing plumbing)

- Build `FilingSection(name="Business Unit / Segment Results (earnings release)",
  excerpt=<region>, full_text_cache_ref=<cached full release>,
  provenance=Provenance(source="SEC EDGAR", source_url=<exhibit url>, as_of=<8-K date>))`
  and append it to `dossier.filing_sections`.
- `filing_sections` **already** flows into the analyst context (equity_research.py
  builds a "FILING SECTIONS:" block from it) — so no context-builder change.
- **Prompt tweak:** the §3 Business Segments instruction gains one line: *when a
  segment / business-unit disclosure is provided, render it as a table and analyze the
  drivers by segment; do not claim segment data is unavailable.*
- **Period-fresh automatically:** each run fetches the *latest* earnings 8-K's exhibit,
  so different report dates surface different periods' segment numbers.

## 5. Edge cases / failure = graceful

- **No earnings 8-K in window / no exhibit-99 found / fetch fails** → no segment section
  appended; §3 stays qualitative (honest, never fabricated). All wrapped so a failure is
  a no-op, not a crash (consistent with the soft-fail dispatcher around `fetch_edgar`).
- **Exhibit naming varies across filers** → name heuristic (`99` + `.htm`, prefer
  `press`/`ex99`); unusual names may be missed → no section (acceptable).
- **Company publishes segments only in the 10-Q, not the release** → no section this
  path (acceptable for the major names, which all publish BU/segment in the release).
- **Region anchor not found** → fall back to the leading `_SEGMENT_MAX_CHARS` of the
  release (press releases front-load highlights), or skip if clearly not a results release.

## 6. Scope

- **Modify:** `saturn/ingestion/edgar_filings.py` (`_find_exhibit_99`, `_extract_segment_region`),
  `saturn/ingestion/edgar.py` (`_fetch_filing_index` + wire the segment section into
  `fetch_edgar`), `saturn/workflows/equity_research.py` (§3 prompt line).
- **Test:** `tests/ingestion/test_edgar_filings.py` (finder + region extractor on a small
  fixture press release), `tests/ingestion/test_edgar.py` (`fetch_edgar` appends the
  segment `FilingSection` when an earnings 8-K + exhibit are present; graceful skip when
  absent).
- No new model; no report-render change (§3 is LLM-authored and improves via context).

## 7. Out of scope

- Structured `SegmentFact`s / a deterministic segment table in the report (Option 3;
  future, once we want segment-level derived metrics).
- Verifying the analyst's transcribed segment numbers — that's the Critic (next).
- 10-Q segment-footnote sourcing (fallback source) — add later if a name needs it.
