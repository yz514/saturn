# `saturn doctor` — Live Dependency Check — Design Spec

**Date:** 2026-06-06
**Status:** Approved (design sign-off); pending spec review → `writing-plans`.
**Author:** Saturn dev workflow (brainstorming skill).
**Branch:** `saturn-doctor` off `main` (independent of other slices).

## Motivation

Saturn's test suite is offline-by-design: parsers are verified against committed fixtures and live fetchers are monkeypatched. That proves the *logic* is correct but never exercises the *real* SEC EDGAR / FRED / yfinance / Anthropic endpoints. Before relying on Saturn for actual research, the user needs a one-command way to confirm credentials are set and the live integrations actually work — and to surface real-world data quirks (messy 10-K HTML, unusual XBRL tags, the FRED key, the SEC User-Agent) that fixtures can't.

`saturn doctor [TICKER]` runs the four live dependency checks and prints a readiness report.

## Scope

**In scope:** a `doctor` CLI subcommand that live-checks all four data dependencies (Anthropic, yfinance, SEC EDGAR, FRED), one isolated check each, with a pass/fail report and a scriptable exit code.

**Out of scope:** retries/backoff; checking optional future sources (FMP/Finnhub); auto-fixing config; any change to the adapters themselves.

## Architecture

- **New `saturn/diagnostics.py`** — pure-ish orchestration + formatting:
  - `CheckResult` (Pydantic): `name: str`, `ok: bool`, `detail: str`.
  - One function per dependency, each wrapping a real adapter call in `try/except` and returning a `CheckResult` (a check never raises):
    - `check_anthropic(settings) -> CheckResult`
    - `check_yfinance(ticker) -> CheckResult`
    - `check_edgar(ticker) -> CheckResult`
    - `check_fred() -> CheckResult`
  - `run_checks(ticker, *, settings) -> list[CheckResult]` — calls the four in order, returns results.
  - `format_report(ticker, results) -> str` — renders the checklist + summary line.
- **`saturn/cli.py`** — add a `doctor` command: resolve settings, call `run_checks`, print `format_report`, and `raise typer.Exit(1)` if any result is not `ok`.

Each check is small, independently testable, and depends only on its one adapter + config.

## The four checks

1. **Anthropic** (`check_anthropic`): if `settings.anthropic_api_key` is falsy → `ok=False`, detail "ANTHROPIC_API_KEY not set". Else construct `AnthropicClient(key, "claude-haiku-4-5")` and call `.complete()` with a trivial system+prompt (e.g. system "You are a health check.", prompt "Reply with the single word: OK") → `ok=True`, detail "key works (haiku responded)". The cheapest model is used deliberately (this only proves the key/credentials work; cost is a few tokens). Any exception → `ok=False`, detail `str(exc)`.
2. **yfinance** (`check_yfinance`): `q = fetch_quote(ticker)` (live, `mock=False`) → `ok = q.price is not None`, detail "price $X, market cap $Y". Exception → `ok=False`.
3. **SEC EDGAR** (`check_edgar`): `r = fetch_edgar(ticker)` → `ok=True`, detail "{name} (CIK {cik}) — {n} facts, {m} sections, {k} events" where counts come from `r["fundamentals"].facts`, `r["filing_sections"]`, `r.get("material_events", [])`. `DataUnavailable` (no UA / no CIK) → `ok=False` with the message; `SourceFailure` → `ok=False`.
4. **FRED** (`check_fred`): `s = fetch_fred()` → `ok=True`, detail "{n} series, e.g. {first.series_id} {latest_value} ({latest_date})". `DataUnavailable` (no key) → `ok=False`.

## Output & exit code

`format_report` renders, e.g.:

```text
Saturn doctor — ticker: AAPL

✓ Anthropic   key works (haiku responded)
✓ yfinance    price $228.50, market cap $3,400,000,000,000
✓ SEC EDGAR   Apple Inc. (CIK 0000320193) — 22 facts, 3 sections, 1 event
✗ FRED        FRED_API_KEY not set

3/4 checks passed.
```

The `doctor` command prints this and exits `0` when all pass, `1` when any check fails (scriptable readiness gate).

## Error handling

- Every check is wrapped so a single failure (missing key, network error, real-world parsing quirk) becomes a `CheckResult(ok=False)` — `run_checks` always returns four results, never raises.
- The Anthropic ping uses the cheapest model and a 1-word-reply prompt to keep cost negligible.
- `doctor` performs live network I/O by design (that is its purpose) — it is the one command that is NOT offline.

## Testing (offline integrity preserved)

- `run_checks` / `format_report` / each `check_*` are unit-tested with the underlying adapter calls **monkeypatched** — success, `DataUnavailable`, `SourceFailure`, and missing-key cases — asserting each `CheckResult.ok`/`detail` and the rendered report text. For `check_anthropic`, monkeypatch `AnthropicClient` (or inject) so no real call is made.
- A CLI test invokes `doctor` via Typer's `CliRunner` with `run_checks` monkeypatched to a fixed result set, asserting the printed report and the exit code (0 all-pass; 1 any-fail).
- The live calls themselves are not unit-tested (same convention as the adapters). The autouse `.env`-neutralizing fixture keeps everything offline.

## Success criteria

- `saturn doctor` with all creds set → four ✓ lines, exit 0, each line showing the real datum fetched.
- With `FRED_API_KEY`/`SEC_USER_AGENT` unset → those lines ✗ with the honest reason, exit 1, others still ✓.
- Full test suite stays green and fully offline.

## Next step

Spec self-review → user review → `writing-plans` → subagent-driven execution.
