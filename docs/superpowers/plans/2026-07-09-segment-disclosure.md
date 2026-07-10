# Segment Disclosure (Option 1) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Ingest the earnings-release business-unit/segment text as a `FilingSection` so §3 can render a real segment table; graceful no-op when absent; period-fresh automatically.

**Design:** `docs/superpowers/specs/2026-07-09-segment-disclosure-design.md`

---

### Task 1: Sourcing helpers (exhibit finder + segment-region extractor)

**Files:**
- Modify: `saturn/ingestion/edgar_filings.py`
- Test: `tests/ingestion/test_edgar_filings.py`

- [ ] **Step 1: Write failing tests**

```python
def test_find_exhibit_99_prefers_press_release():
    items = [{"name": "mu-20260624.htm"}, {"name": "a2026q3ex991-pressrelease.htm"},
             {"name": "R1.htm"}, {"name": "mu-20260624.xsd"}]
    assert _find_exhibit_99(items) == "a2026q3ex991-pressrelease.htm"


def test_find_exhibit_99_none_when_absent():
    assert _find_exhibit_99([{"name": "mu-20260624.htm"}, {"name": "R1.htm"}]) is None


def test_extract_segment_region_isolates_bu_table():
    text = ("Micron reported record revenue of $41.456B. " + "filler " * 50 +
            "Quarterly Business Unit Financial Results FQ3-26 "
            "Cloud Memory Business Unit Revenue $ 13,769 Gross margin 83 % "
            "Core Data Center Business Unit Revenue $ 11,524 " + "tail " * 2000)
    region = _extract_segment_region(text)
    assert region is not None
    assert "Cloud Memory" in region and "Core Data Center" in region
    assert len(region) <= 6000


def test_extract_segment_region_none_without_anchor():
    assert _extract_segment_region("Just a dividend announcement, no unit tables here.") is None
```

- [ ] **Step 2: Run to verify they fail.**

- [ ] **Step 3: Implement in `saturn/ingestion/edgar_filings.py`**

```python
_SEGMENT_MAX_CHARS = 6000


def _find_exhibit_99(index_items: list[dict]) -> str | None:
    """Pick the exhibit-99 press-release document from a filing's index.json items."""
    cands = [it.get("name", "") for it in index_items
             if it.get("name", "").lower().endswith((".htm", ".html")) and "99" in it.get("name", "").lower()]
    if not cands:
        return None
    pref = [n for n in cands if any(k in n.lower() for k in ("press", "ex99", "ex-99", "991"))]
    return (pref or cands)[0]


def _extract_segment_region(text: str, max_chars: int = _SEGMENT_MAX_CHARS) -> str | None:
    """Isolate the business-unit / segment region of a stripped press release, with a
    little leading context, capped. None when no segment table is present."""
    if not text:
        return None
    m = re.search(r"business unit|reportable segment|segment (results|information|revenue)", text, re.IGNORECASE)
    if not m:
        return None
    start = max(0, m.start() - 300)
    return text[start:start + max_chars]
```

- [ ] **Step 4: Run tests to verify they pass; then full suite.**

- [ ] **Step 5: Commit** `fix(edgar): exhibit-99 finder + segment-region extractor`

---

### Task 2: Wire the segment section into fetch_edgar + §3 prompt

**Files:**
- Modify: `saturn/ingestion/edgar.py`, `saturn/workflows/equity_research.py`
- Test: `tests/ingestion/test_edgar.py`

- [ ] **Step 1: Write failing test** (in `tests/ingestion/test_edgar.py`)

```python
def test_fetch_edgar_appends_segment_section(monkeypatch):
    cf, sub = _companyfacts(), _submissions()
    # ensure submissions has an earnings 8-K (item 2.02); if the NVDA fixture lacks one,
    # monkeypatch _select_recent_8ks to return one.
    earnings = {"form": "8-K", "accession": "0000723125-26-000013", "primary_document": "x.htm",
                "filing_date": "2026-06-24", "item_codes": ["2.02", "9.01"]}
    monkeypatch.setattr("saturn.ingestion.edgar.ticker_to_cik", lambda t: "0001045810")
    monkeypatch.setattr("saturn.ingestion.edgar._fetch_companyfacts", lambda cik: cf)
    monkeypatch.setattr("saturn.ingestion.edgar._fetch_submissions", lambda cik: sub)
    monkeypatch.setattr("saturn.ingestion.edgar._fetch_filing_html", lambda cik, accn, doc: _tenk_html())
    monkeypatch.setattr("saturn.ingestion.edgar._cache_full_text", lambda *a, **k: "cache://ref")
    monkeypatch.setattr("saturn.ingestion.edgar._select_recent_8ks", lambda sub, since: [earnings])
    monkeypatch.setattr("saturn.ingestion.edgar._fetch_filing_index",
                        lambda cik, accn: [{"name": "ex991-press.htm"}])
    monkeypatch.setattr("saturn.ingestion.edgar._fetch_filing_html",
                        lambda cik, accn, doc: "<p>Quarterly Business Unit Financial Results: Cloud Memory Revenue $13,769 Gross margin 83%</p>")
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
```

- [ ] **Step 2: Run to verify they fail.**

- [ ] **Step 3: Implement in `saturn/ingestion/edgar.py`**

Add to the imports from `edgar_filings`: `_find_exhibit_99`, `_extract_segment_region`, `_strip_html`.
Add helpers:
```python
def _fetch_filing_index(cik: str, accession: str) -> list[dict]:
    url = _ARCHIVE_URL.format(cik_int=int(cik), accn_nodash=accession.replace("-", ""), doc="index.json")
    data = json.loads(http_get(url, user_agent=_ua(), accept="application/json"))
    return (data.get("directory", {}) or {}).get("item", []) or []


def _fetch_segment_section(cik: str, earnings_8k: dict) -> FilingSection | None:
    """Best-effort: the BU/segment table text from the earnings-release exhibit 99.
    Returns None (never raises) when unavailable."""
    try:
        doc = _find_exhibit_99(_fetch_filing_index(cik, earnings_8k["accession"]))
        if not doc:
            return None
        text = _strip_html(_fetch_filing_html(cik, earnings_8k["accession"], doc))
        region = _extract_segment_region(text)
        if not region:
            return None
        url = _ARCHIVE_URL.format(cik_int=int(cik), accn_nodash=earnings_8k["accession"].replace("-", ""), doc=doc)
        as_of = date.fromisoformat(earnings_8k["filing_date"]) if earnings_8k.get("filing_date") else None
        ref = _cache_full_text(cik, f"segment_{earnings_8k['accession']}", text)
        return FilingSection(
            name="Business Unit / Segment Results (earnings release)",
            excerpt=region, full_text_cache_ref=ref,
            provenance=Provenance(source="SEC EDGAR", source_url=url, as_of=as_of),
        )
    except Exception as exc:  # noqa: BLE001 - segment disclosure is optional
        logger.debug("segment disclosure unavailable for %s: %s", cik, exc)
        return None
```
In `fetch_edgar`, after the 8-K loop and before assembling the return, add:
```python
    earnings = next((e for e in _select_recent_8ks(submissions, since=since) if "2.02" in e["item_codes"]), None)
    if earnings:
        seg = _fetch_segment_section(cik, earnings)
        if seg:
            filing_sections.append(seg)
```

- [ ] **Step 4: §3 prompt tweak** in `saturn/workflows/equity_research.py`

In the analysis system prompt, add one instruction (near the business_segments guidance):
> When a "Business Unit / Segment Results" disclosure appears in FILING SECTIONS, render it as a markdown table (segment, revenue, gross/operating margin) and analyze the growth drivers by segment. Never state that segment data is unavailable when such a disclosure is provided.

- [ ] **Step 5: Run report/workflow + full suite — all green.**

- [ ] **Step 6: Commit** `feat(edgar): ingest earnings-release segment disclosure into context`

---

## Final verification (live)

```bash
.venv/Scripts/python.exe -c "from saturn.ingestion.dossier import build_dossier; d=build_dossier('MU'); \
seg=[s for s in d.filing_sections if s.name.startswith('Business Unit / Segment')]; \
print(seg[0].excerpt[:400] if seg else 'NO SEGMENT SECTION'); print('source:', seg[0].provenance.source_url if seg else None)"
```
Expect the MU Cloud Memory / Core Data Center BU numbers + the exhibit URL. Then dispatch a final reviewer and finish the branch (PR to `main`).
