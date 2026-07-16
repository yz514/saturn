# Gap Honesty: record what a source didn't contribute, and refuse to research without fundamentals — Design

**Date:** 2026-07-15
**Status:** Approved (brainstorm) → ready for plan
**Found by:** a live ASML run, 2026-07-15.

## 1. Goal & honest framing

`SourceGap`'s contract is *"a source that could not contribute, recorded instead of crashing."* EDGAR
violates it silently. A live ASML dossier:

```
all periods: []          ← ZERO fundamentals
filing_sections: []      ← ZERO filings
material_events: 0       ← ZERO events
driver_model: None
GAPS: []                 ← no gap recorded, despite EDGAR contributing nothing
```

**Root cause (verified).** `route_to_source` creates a gap **only in its `except` clauses**:

```python
def route_to_source(source, fetch) -> tuple[T | None, SourceGap | None]:
    """Call `fetch`; return (result, None) on success or (None, gap) on failure."""
    try:
        return fetch(), None          # "success" == "didn't raise"
    except IngestionError as exc:
        return None, SourceGap(source=source, reason=str(exc))
```

EDGAR **didn't raise**: it fetched ASML's companyfacts fine, parsed fine, and then the form filters
(`edgar.py:97` `if not form.startswith("10-K")`, `edgar.py:108` `... not form.startswith("10-Q")`)
dropped every row, because **ASML is a foreign private issuer that files only 20-F**:

```
RevenueFromContractWithCustomerExcludingAssessedTax [EUR]: 24 rows | forms={'20-F': 24}
NetIncomeLoss [EUR]: 51 rows | forms={'20-F/A': 6, '20-F': 45}
```

Zero facts is indistinguishable from success, so the dossier presented itself as complete.

**This is not a missing mechanism — it is one source not honouring an existing contract.** The
`industry` source already raises when it has nothing to give, and its gap *is* recorded:
`source industry unavailable: no value-chain peers for MSFT (industry 'Software - Infrastructure')`.
EDGAR simply never learned to do this.

**Scope boundary.** This slice makes Saturn **honest** about the hole. It does **not** add
foreign-private-issuer support (20-F/6-K ingestion + EUR→USD normalisation) — that is a much larger
slice, and shipping it *without* this one would produce confidently wrong numbers (ASML's XBRL is in
**EUR** while its quote is **USD**). Honesty first.

## 2. Part 1 — EDGAR raises a diagnostic `DataUnavailable`

**Which exception.** `saturn/ingestion/errors.py` already distinguishes `DataUnavailable` — *"the
source responded but the requested datum does not exist"* — from `SourceFailure` (transport). ASML is
exactly the former, so raise **`DataUnavailable`** (a subclass of `IngestionError`, so
`route_to_source` catches it unchanged).

**Where.** Not in `_period_entries(tag_block, unit, *, annual)` — that filters **one** concept, and a
single absent concept is normal. "Zero facts overall" is only knowable where `Fundamentals` is
assembled, in `fetch_fundamentals`.

**What.** After assembling the facts, if none survived, survey the raw blob so the reason states what
was actually there:

```python
def _survey_forms(blob: dict) -> tuple[int, list[str]]:
    """Count us-gaap XBRL rows and their distinct SEC forms — for a diagnostic gap reason.
    Pure; (0, []) when the blob carries no us-gaap facts."""
    forms: set[str] = set()
    n = 0
    for tag_block in ((blob.get("facts") or {}).get("us-gaap") or {}).values():
        for rows in (tag_block.get("units") or {}).values():
            for row in rows:
                n += 1
                form = row.get("form")
                if form:
                    forms.add(str(form))
    return n, sorted(forms)
```

and then, at the point `fetch_fundamentals` would return an empty `Fundamentals`:

```python
    if not facts:
        n_rows, forms = _survey_forms(raw)
        if n_rows == 0:
            raise DataUnavailable("no XBRL facts published for this company")
        raise DataUnavailable(
            f"0 usable facts from {n_rows:,} XBRL rows (forms seen: {', '.join(forms)}); "
            f"Saturn reads 10-K/10-Q only")
```

For ASML this produces a gap reason naming the cause:
`edgar: 0 usable facts from N XBRL rows (forms seen: 20-F, 20-F/A); Saturn reads 10-K/10-Q only`

The reader learns not merely *that* EDGAR failed but *why* — and implicitly that Saturn cannot cover
foreign issuers today. **No dispatcher change**: `route_to_source` already converts this into a
recorded gap, and `build_dossier` already tolerates a `None` edgar result.

## 3. Part 2 — the CLI refuses to research without fundamentals

Recording the gap makes the dossier honest, but `run()` would still spend **5–11 Opus calls (~10 min)**
reasoning over no fundamentals. The decisive argument is not cost: **the Critic grounds its numeric
audit against as-reported facts, so with zero facts Saturn's principal safety mechanism is inert.** A
no-fundamentals report is not merely thin — it is *unguarded*, which is the exact hallucination surface
this codebase has been closing.

In `saturn/cli.py`, between the existing `company = build_dossier(...)` and `report = run(...)`:

```python
    if not company.fundamentals.facts:
        typer.echo(f"{ticker}: insufficient data to research.", err=True)
        for g in company.gaps:
            typer.echo(f"  {g.source}: {g.reason}", err=True)
        typer.echo("No report written.", err=True)
        raise typer.Exit(1)
```

Yielding:
```
$ saturn research ASML
ASML: insufficient data to research.
  edgar: 0 usable facts from N XBRL rows (forms seen: 20-F, 20-F/A). Saturn reads 10-K/10-Q only
No report written.
```

**Two deliberate boundaries:**
- **The gate lives in the CLI, not `run()`.** `run()` is called directly by the test suite and by
  offline scripts with hand-built dossiers; gating there would be a wide blast radius for no benefit.
  The CLI is the entry point that spends money.
- **The condition is `fundamentals.facts` being empty — not "any gap exists."** A missing FRED or
  industry source must not block a report (both are routinely absent and already recorded); only absent
  fundamentals make the Critic inert.

`--mock` is unaffected: `_mock_dossier` carries facts, so the gate never fires on it.

## 4. Testing

- **`_survey_forms` (unit, pure):** a blob whose rows are all `20-F` → `(n, ["20-F"])`; a mixed blob →
  sorted distinct forms; a blob with no `us-gaap` key → `(0, [])`.
- **`fetch_fundamentals`:** with a stubbed companyfacts blob whose rows are **all 20-F**, raises
  `DataUnavailable` whose message contains `"20-F"` and `"10-K/10-Q"`; with a blob carrying **no**
  us-gaap facts, raises `DataUnavailable("no XBRL facts published…")`; **a normal 10-K/10-Q blob still
  returns facts and does NOT raise** (the regression guard — this must not break every US ticker).
- **`build_dossier`:** an ASML-like stub (EDGAR raising `DataUnavailable`) yields a dossier that
  **records an `edgar` gap and does not crash**, with `fundamentals.facts == []` — proving the raise
  becomes a gap rather than an exception.
- **CLI (the "0 LLM calls" promise):** invoke the `research` command with `build_dossier` monkeypatched
  to return a facts-less dossier carrying an `edgar` gap; assert exit code 1, that stderr names the gap
  reason, and — critically — that **`run` was never called** (monkeypatch it to a sentinel that fails
  the test if invoked).
- **Live:** `saturn research ASML` prints the refusal above, writes no report, and makes zero LLM calls.

## 5. Scope

- **Modify:** `saturn/ingestion/edgar.py` (`_survey_forms` + the raise in `fetch_fundamentals`),
  `saturn/cli.py` (the gate); touched tests. **No change** to `dispatch.py`, `models.py`, the renderer,
  or `run()`.

## 6. Out of scope

- **Foreign-private-issuer support** (20-F/6-K forms, 6-K interim results) and **currency
  normalisation** (ASML reports EUR; its ADR quote is USD — ingesting without converting would silently
  mix EUR earnings with a USD price and yield confidently wrong per-share metrics). This is the natural
  follow-on slice and unlocks TSM, SAP, BABA, TM, SONY, NVO, AZN, SHOP.
- Recording gaps for *partial* EDGAR output (e.g. facts present but zero filing sections), and a
  dispatcher-level `is_empty` predicate to prevent this bug class structurally. Both considered and
  deferred: this slice fixes the one source that breaks the contract, following the precedent
  `industry` already sets.
