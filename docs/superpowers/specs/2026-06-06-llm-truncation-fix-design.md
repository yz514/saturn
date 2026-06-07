# Fix: analyze/debate JSON truncation under enriched context — Design Spec

**Date:** 2026-06-06
**Status:** Approved (design sign-off); pending spec review → `writing-plans`.
**Author:** Saturn dev workflow (brainstorming skill).
**Branch:** `fix-llm-truncation` off `main`.

## Motivation

The first live `saturn research AAPL` run crashed at the `analyze` step:

```
Invalid JSON: EOF while parsing a string ...
input_value='{\n  "executive_summary"..., Apple TV+, etc.),\nand'   ← truncated mid-sentence
```

Root cause: the enriched `CompanyDossier` now renders a very large grounded context into the analyze/debate prompts (327 facts + 4 filing-section excerpts up to 4,000 chars each + 10 8-K event excerpts). `AnthropicClient` hardcodes `max_tokens=2000`, so the model's longer analysis JSON is truncated mid-string and `model_validate_json` raises — and `cli.py` only catches `IngestionError`, so the command dies with a stack trace. The offline `--mock` path can't surface this (it returns tiny fixed JSON). `saturn research <real ticker>` is currently broken.

This fixes it with three coordinated changes: more output room, a bounded LLM-facing context (also controls token cost), and graceful failure.

## Scope

**In scope:** thread `max_tokens` through the LLM interface; bound what `_company_context` renders into the prompt; add a typed `LLMResponseError` with graceful CLI handling.

**Out of scope:** retry/backoff on truncation (YAGNI — bounding + headroom should prevent it); LLM-side summarization of filings; changing the `CompanyDossier`, the report renderer, or the cache (they keep full data); streaming responses.

## §1. Output room — `max_tokens` through the LLM interface

- `saturn/llm/base.py`: extend the Protocol to `complete(self, system: str, prompt: str, *, model: str | None = None, max_tokens: int = 2000) -> str`.
- `saturn/llm/anthropic_client.py`: `complete` accepts `max_tokens` and passes it to `_build_params` (which already has the parameter). Default stays 2000 for any other caller.
- `saturn/llm/mock_client.py`: `complete` accepts `max_tokens` (ignored).
- `saturn/workflows/equity_research.py`: a module constant `_MAX_OUTPUT_TOKENS = 4096`; `analyze` and `debate` call `llm.complete(..., max_tokens=_MAX_OUTPUT_TOKENS)`. 4096 is ample for the 10 structured string fields once the context is bounded, and easy to tune.

## §2. Bounded LLM-facing context (prompt-only)

The dossier, report, and cache keep full data. Only `_company_context` (the text fed to the model) is trimmed, via module constants in `equity_research.py`:

- `_CTX_MAX_ANNUAL = 3`, `_CTX_MAX_QUARTERS = 4`: per concept, render only the most-recent 3 annual + 4 quarterly `FinancialFact`s (sort by fiscal period; annual = `FY####`, quarterly = `Q# FY####`). 327 facts → a focused recent set.
- `_CTX_SECTION_CHARS = 1200`: each `FilingSection` excerpt is sliced to the first 1,200 chars in the prompt.
- `_CTX_MAX_EVENTS = 6`, `_CTX_EVENT_CHARS = 500`: render at most the 6 most-recent material events; each event excerpt sliced to 500 chars.

These caps live in `_company_context` only; nothing else changes. This addresses truncation at the source (less input → less verbose output) and cuts per-call token cost.

## §3. Graceful failure

- New `LLMResponseError(RuntimeError)` in `saturn/workflows/equity_research.py`.
- In `analyze` and `debate`, wrap the `AnalysisSections.model_validate_json(_extract_json(raw))` / `DebateSections...` call in `try/except (ValueError, pydantic.ValidationError)` (note: `json.JSONDecodeError` is a `ValueError`, and pydantic's `model_validate_json` raises `ValidationError`) → raise `LLMResponseError(f"model returned malformed or truncated JSON for {schema}")` chained from the original.
- `saturn/cli.py` `research`: catch `LLMResponseError` (in addition to `IngestionError`) → `typer.echo(str(exc), err=True)` + `raise typer.Exit(1)`. No stack trace.

## §4. Testing (offline)

- **max_tokens threading:** assert `_build_params(system, prompt, model, max_tokens=4096)["max_tokens"] == 4096`; assert `MockLLMClient.complete(..., max_tokens=4096)` works (ignored).
- **context bounding:** build a `CompanyDossier` (or extend `_mock_dossier`) with > the caps — e.g. 6 annual + 6 quarterly Revenues facts, a filing section with a long excerpt, 10 events — and assert `_company_context` renders only the most-recent 3 annual + 4 quarterly periods, the section excerpt is ≤ 1,200 chars in the output, and ≤ 6 events appear. Capture the actual most-recent labels and assert older ones are absent.
- **graceful failure:** a fake `LLMClient` whose `complete` returns truncated JSON (e.g. `'{"executive_summary": "abc'`) → `analyze(...)` raises `LLMResponseError`; a CLI test (CliRunner, with `build_dossier` + LLM monkeypatched to produce that) → exit code 1 and a clean message in output, no traceback.
- Full suite stays offline (no real LLM/network).

## Success criteria

- With these changes, a real `saturn research <ticker>` completes and writes the report (verified manually post-merge with creds), instead of crashing on truncated JSON.
- A genuinely malformed/truncated LLM response yields a clean `Exit(1)` message, not a stack trace.
- Per-call prompt size is materially smaller (recent-only facts + trimmed excerpts), reducing token cost.
- Full offline suite green.

## Next step

Spec self-review → user review → `writing-plans` → subagent-driven execution.
