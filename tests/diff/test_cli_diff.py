import json
from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner

from seo_scout.cli import app

runner = CliRunner()
HOOK = "https://hooks.slack.com/services/T000/B000/XXXX"


def mock_site(title: str) -> None:
    respx.get("https://e.com/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://e.com/sitemap.xml").mock(return_value=httpx.Response(404))
    respx.get("https://e.com/").mock(
        return_value=httpx.Response(
            200, html=f"<html><head><title>{title}</title></head><body>x</body></html>"
        )
    )


@respx.mock
def test_diff_prints_table_and_json() -> None:
    mock_site("Short")
    assert runner.invoke(app, ["crawl", "https://e.com/", "--delay", "0", "--no-ai"]).exit_code == 0
    mock_site("A Much Better Title Of A Sensible Length")
    assert runner.invoke(app, ["crawl", "https://e.com/", "--delay", "0", "--no-ai"]).exit_code == 0
    table = runner.invoke(app, ["diff", "1", "2"])
    assert table.exit_code == 0, table.output
    assert "run 1 -> run 2" in table.stdout
    assert "title_too_short" in table.stdout
    as_json = runner.invoke(app, ["diff", "1", "2", "--json"])
    assert as_json.exit_code == 0
    body = json.loads(as_json.stdout)
    assert body["issues_fixed"][0]["rule_id"] == "title_too_short"


def test_diff_rejects_unknown_runs() -> None:
    result = runner.invoke(app, ["diff", "1", "2"])
    assert result.exit_code != 0


@respx.mock
def test_report_crawls_diffs_writes_files_and_posts_to_slack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_site("Short")
    assert runner.invoke(app, ["crawl", "https://e.com/", "--delay", "0", "--no-ai"]).exit_code == 0
    mock_site("A Much Better Title Of A Sensible Length")
    hook = respx.post(HOOK).mock(return_value=httpx.Response(200, text="ok"))
    monkeypatch.setenv("SLACK_WEBHOOK_URL", HOOK)
    result = runner.invoke(
        app, ["report", "https://e.com/", "--delay", "0", "--no-ai", "--out", "reports"]
    )
    assert result.exit_code == 0, result.output
    assert "run 1 -> run 2" in result.stdout
    assert Path("reports/run-2.json").exists()
    assert Path("reports/run-2.md").read_text(encoding="utf-8").startswith("# ")
    assert hook.called
    assert "e.com" in json.loads(hook.calls.last.request.content)["text"]
    assert "slack: posted" in result.output


@respx.mock
def test_report_with_no_previous_run_still_writes_a_baseline() -> None:
    mock_site("Short")
    result = runner.invoke(
        app, ["report", "https://e.com/", "--delay", "0", "--no-ai", "--out", "reports"]
    )
    assert result.exit_code == 0, result.output
    assert "baseline" in result.output.lower()
    assert Path("reports/run-1.json").exists()
