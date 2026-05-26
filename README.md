# Saturn

An AI-native autonomous equity research platform. Saturn coordinates a pipeline
(and, over time, multiple specialized agents) to ingest financial data, analyze
companies, debate theses, and generate research reports.

> Research and educational use only. Not investment advice.

## Quickstart

```bash
python -m venv .venv
.venv\Scripts\Activate.ps1          # Windows PowerShell
python -m pip install -e ".[dev]"

# Offline demo (no API key needed):
saturn research NVDA --mock

# Real run (set ANTHROPIC_API_KEY in .env first):
saturn research NVDA
```

Reports are written to `reports/<TICKER>_<YYYY-MM-DD>.md`.
See `examples/nvda_research_report.md` for sample output.

## Status

Phase 0 (foundation + MVP). See `docs/roadmap.md` for the full plan and
`docs/superpowers/specs/` + `docs/superpowers/plans/` for design and build docs.

## Development

```bash
python -m pytest          # offline test suite (no network, no API key)
```
