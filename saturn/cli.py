"""Saturn command-line interface."""

from __future__ import annotations

from datetime import date

import typer

from saturn.config import get_settings
from saturn.diagnostics import format_report, run_checks
from saturn.ingestion.dossier import build_dossier
from saturn.ingestion.errors import IngestionError
from saturn.llm.anthropic_client import AnthropicClient
from saturn.llm.mock_client import MockLLMClient
from saturn.reports.markdown_report import render
from saturn.utils.logging import setup_logging
from saturn.workflows.equity_research import LLMResponseError, run

app = typer.Typer(help="Saturn — autonomous equity research.")


@app.callback()
def _main() -> None:
    """Saturn — autonomous equity research (keeps `research` as a subcommand)."""


@app.command()
def research(
    ticker: str = typer.Argument(..., help="Stock ticker, e.g. NVDA"),
    mock: bool = typer.Option(False, "--mock", help="Run fully offline with sample data."),
    model: str | None = typer.Option(None, "--model", help="Override the LLM model."),
) -> None:
    """Generate a markdown equity research report for TICKER."""
    settings = get_settings()
    setup_logging(settings.log_level)
    ticker = ticker.upper()

    # Resolve the LLM client first so config errors (e.g. missing key) fail
    # fast, before any network ingestion. This also keeps the no-key path
    # fully offline for tests.
    if mock:
        llm = MockLLMClient()
        model_used = "mock"
    else:
        if not settings.anthropic_api_key:
            typer.echo(
                "ANTHROPIC_API_KEY not set. Copy .env.example to .env, "
                "or run with --mock for offline output.",
                err=True,
            )
            raise typer.Exit(1)
        model_used = model or settings.default_model
        llm = AnthropicClient(settings.anthropic_api_key, model_used)

    try:
        company = build_dossier(ticker, mock=mock)
    except IngestionError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)

    # Without as-reported facts the Critic can ground nothing, so a report would be unguarded — not
    # merely thin. Refuse before spending any LLM call, and name the source that came back empty.
    if not (company.fundamentals and company.fundamentals.facts):
        typer.echo(f"{ticker}: insufficient data to research.", err=True)
        for g in company.gaps:
            typer.echo(f"  {g.source}: {g.reason}", err=True)
        typer.echo("No report written.", err=True)
        raise typer.Exit(1)

    try:
        report = run(company, llm, model_used=model_used, mock=mock)
    except LLMResponseError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)
    markdown = render(report)

    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    out_path = settings.reports_dir / f"{ticker}_{date.today():%Y-%m-%d}.md"
    out_path.write_text(markdown, encoding="utf-8")

    banner = "[MOCK MODE] " if mock else ""
    typer.echo(f"{banner}Wrote {out_path}")


@app.command()
def doctor(
    ticker: str = typer.Argument("AAPL", help="Ticker to live-check, e.g. AAPL"),
) -> None:
    """Live-check Saturn's data dependencies (Anthropic, yfinance, EDGAR, FRED)."""
    settings = get_settings()
    setup_logging(settings.log_level)
    ticker = ticker.upper()
    results = run_checks(ticker, settings=settings)
    typer.echo(format_report(ticker, results))
    if any(not r.ok for r in results):
        raise typer.Exit(1)


@app.command()
def metrics(
    write: bool = typer.Option(False, "--write", help="Regenerate docs/metrics.md from the catalog."),
) -> None:
    """Print the derived-metric reference (or regenerate docs/metrics.md)."""
    import saturn.analytics.catalog as catalog

    content = catalog.render_metrics_reference()
    if write:
        catalog.METRICS_DOC_PATH.write_text(content, encoding="utf-8")
        typer.echo(f"Wrote {catalog.METRICS_DOC_PATH}")
    else:
        typer.echo(content)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
