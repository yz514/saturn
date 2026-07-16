from typer.testing import CliRunner

from saturn.cli import app
from saturn.diagnostics import CheckResult

runner = CliRunner()


def test_research_mock_writes_report(tmp_path, monkeypatch):
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path))
    result = runner.invoke(app, ["research", "nvda", "--mock"])
    assert result.exit_code == 0, result.output
    files = list(tmp_path.glob("NVDA_*.md"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "# NVDA Equity Research Report" in text
    assert "not investment advice" in text
    assert "MOCK MODE" in result.output


def test_research_real_without_key_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = runner.invoke(app, ["research", "NVDA"])
    assert result.exit_code == 1
    assert "ANTHROPIC_API_KEY" in result.output


def test_doctor_all_pass_exit_zero(monkeypatch):
    monkeypatch.setattr(
        "saturn.cli.run_checks",
        lambda ticker, *, settings: [
            CheckResult(name="Anthropic", ok=True, detail="key works"),
            CheckResult(name="yfinance", ok=True, detail="price $1"),
        ],
    )
    result = CliRunner().invoke(app, ["doctor", "AAPL"])
    assert result.exit_code == 0
    assert "2/2 checks passed." in result.stdout


def test_doctor_any_fail_exit_one(monkeypatch):
    monkeypatch.setattr(
        "saturn.cli.run_checks",
        lambda ticker, *, settings: [
            CheckResult(name="Anthropic", ok=True, detail="key works"),
            CheckResult(name="FRED", ok=False, detail="FRED_API_KEY not set"),
        ],
    )
    result = CliRunner().invoke(app, ["doctor"])  # default ticker
    assert result.exit_code == 1
    assert "[FAIL]" in result.stdout


def test_research_handles_llm_response_error(monkeypatch):
    from types import SimpleNamespace
    from typer.testing import CliRunner

    from saturn.cli import app
    from saturn.workflows.equity_research import LLMResponseError

    monkeypatch.setenv("ANTHROPIC_API_KEY", "testkey")
    # Stand-in dossier: must look enough like a real one to pass the
    # fundamentals gate (non-empty facts) so the LLMResponseError path
    # under test is actually reached.
    fake_dossier = SimpleNamespace(fundamentals=SimpleNamespace(facts=[object()]), gaps=[])
    monkeypatch.setattr("saturn.cli.build_dossier", lambda ticker, *, mock: fake_dossier)
    monkeypatch.setattr("saturn.cli.AnthropicClient", lambda *a, **k: object())

    def boom(*a, **k):
        raise LLMResponseError("model returned malformed or truncated JSON for analysis")

    monkeypatch.setattr("saturn.cli.run", boom)

    result = CliRunner().invoke(app, ["research", "NVDA"])
    assert result.exit_code == 1
    assert "malformed or truncated JSON" in result.output
    assert "Traceback" not in result.output


def test_cli_refuses_without_fundamentals_and_never_calls_run(monkeypatch):
    # The ASML case: no fundamentals => the Critic can ground nothing => spend ZERO LLM calls.
    from datetime import date
    import saturn.cli as cli
    from saturn.models import CompanyDossier, SourceGap

    d = CompanyDossier(
        ticker="ASML", name="ASML Holding N.V.", generated_at=date.today(),
        fundamentals=None,
        gaps=[SourceGap(source="edgar",
                        reason="0 usable facts from 24 XBRL rows (forms seen: 20-F); "
                               "Saturn reads 10-K/10-Q only")])
    monkeypatch.setattr(cli, "build_dossier", lambda ticker, mock=False: d)

    def _never_run(*args, **kwargs):
        raise AssertionError("run() must not be called when fundamentals are absent")
    monkeypatch.setattr(cli, "run", _never_run)

    result = runner.invoke(cli.app, ["research", "ASML", "--mock"])
    assert result.exit_code == 1
    assert "insufficient data" in result.output
    assert "20-F" in result.output              # the gap reason reaches the user
    assert "No report written" in result.output


def test_metrics_command_prints_reference():
    from typer.testing import CliRunner
    from saturn.cli import app

    result = CliRunner().invoke(app, ["metrics"])
    assert result.exit_code == 0
    assert "Saturn Metric Definitions" in result.stdout
    assert "gross_margin" in result.stdout


def test_metrics_command_write_regenerates_doc(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from saturn.cli import app
    import saturn.analytics.catalog as catalog

    target = tmp_path / "metrics.md"
    monkeypatch.setattr(catalog, "METRICS_DOC_PATH", target)
    result = CliRunner().invoke(app, ["metrics", "--write"])
    assert result.exit_code == 0
    assert target.read_text(encoding="utf-8") == catalog.render_metrics_reference()
