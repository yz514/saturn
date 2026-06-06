"""Saturn command-line interface."""

from __future__ import annotations

from datetime import date

import typer

from saturn.config import get_settings
from saturn.ingestion.dossier import build_dossier
from saturn.ingestion.errors import IngestionError
from saturn.llm.anthropic_client import AnthropicClient
from saturn.llm.mock_client import MockLLMClient
from saturn.reports.markdown_report import render
from saturn.utils.logging import setup_logging
from saturn.workflows.equity_research import run

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

    report = run(company, llm, model_used=model_used, mock=mock)
    markdown = render(report)

    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    out_path = settings.reports_dir / f"{ticker}_{date.today():%Y-%m-%d}.md"
    out_path.write_text(markdown, encoding="utf-8")

    banner = "[MOCK MODE] " if mock else ""
    typer.echo(f"{banner}Wrote {out_path}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
