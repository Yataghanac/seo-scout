from contextlib import closing
from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner

from seo_scout import cli
from seo_scout.cli import app
from seo_scout.store import db, repo_runs

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "0.1.0"


@respx.mock
def test_crawl_dry_run_prints_seeds_and_fetches_nothing() -> None:
    respx.get("https://e.com/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://e.com/sitemap.xml").mock(
        return_value=httpx.Response(
            200,
            text='<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            "<url><loc>https://e.com/a</loc></url></urlset>",
        )
    )
    home = respx.get("https://e.com/").mock(return_value=httpx.Response(200, html="<p>x</p>"))
    result = runner.invoke(app, ["crawl", "https://e.com/", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "https://e.com/a" in result.stdout
    assert not home.called


@respx.mock
def test_crawl_writes_a_run_to_the_configured_db() -> None:
    respx.get("https://e.com/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://e.com/sitemap.xml").mock(return_value=httpx.Response(404))
    respx.get("https://e.com/").mock(return_value=httpx.Response(200, html='<a href="/a">a</a>'))
    respx.get("https://e.com/a").mock(return_value=httpx.Response(200, html="<p>a</p>"))
    result = runner.invoke(app, ["crawl", "https://e.com/", "--delay", "0", "--db", "t.db"])
    assert result.exit_code == 0, result.output
    assert "run 1" in result.stdout
    assert "audit: 2 pages" in result.stdout
    assert "ai: 2 pages considered" in result.stdout
    assert "OPENAI_API_KEY unset" in result.output
    with closing(db.connect("t.db")) as conn:
        runs = repo_runs.list_runs(conn)
    assert len(runs) == 1
    assert runs[0].status == "complete"
    assert runs[0].pages == 2


def test_crawl_rejects_non_http_url() -> None:
    result = runner.invoke(app, ["crawl", "mailto:x@y.z"])
    assert result.exit_code != 0
    assert "http" in result.output


def test_audit_command_reaudits_an_existing_run() -> None:
    test_crawl_writes_a_run_to_the_configured_db()
    result = runner.invoke(app, ["audit", "1", "--db", "t.db"])
    assert result.exit_code == 0, result.output
    assert "audit: 2 pages" in result.stdout


def test_audit_command_rejects_unknown_run() -> None:
    result = runner.invoke(app, ["audit", "42", "--db", "t.db"])
    assert result.exit_code != 0
    assert "42" in result.output


def test_ai_command_without_key_degrades_cleanly() -> None:
    test_crawl_writes_a_run_to_the_configured_db()
    result = runner.invoke(app, ["ai", "1", "--db", "t.db"])
    assert result.exit_code == 0, result.output
    assert "2 skipped" in result.stdout
    assert "deterministic results only" in result.output


@respx.mock
def test_crawl_no_ai_flag_skips_the_stage() -> None:
    respx.get("https://e.com/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://e.com/sitemap.xml").mock(return_value=httpx.Response(404))
    respx.get("https://e.com/").mock(return_value=httpx.Response(200, html="<p>x</p>"))
    result = runner.invoke(app, ["crawl", "https://e.com/", "--delay", "0", "--no-ai"])
    assert result.exit_code == 0, result.output
    assert "ai:" not in result.stdout


REPO_ENV_EXAMPLE = Path(__file__).resolve().parents[1] / ".env.example"


def test_packaged_env_template_matches_the_repo_example() -> None:
    """One template, shipped in the wheel; the repo copy exists for humans browsing GitHub."""
    assert cli.env_template() == REPO_ENV_EXAMPLE.read_text(encoding="utf-8")


def test_init_writes_the_packaged_template_from_any_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)  # no .env.example here, as after `uv tool install`
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / ".env").read_text(encoding="utf-8") == cli.env_template()
    assert "OPENAI_API_KEY" in result.output
    assert "crawl" in result.output


def test_init_never_overwrites_an_existing_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env.example").write_text("OPENAI_API_KEY=\n")
    (tmp_path / ".env").write_text("OPENAI_API_KEY=sk-keep\n")
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / ".env").read_text() == "OPENAI_API_KEY=sk-keep\n"
    assert "already exists" in result.output


class FakeTimer:
    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1", "http://127.0.0.1:8000"),
        ("0.0.0.0", "http://127.0.0.1:8000"),
        ("::", "http://[::1]:8000"),
        ("::1", "http://[::1]:8000"),
        ("localhost", "http://localhost:8000"),
        ("2001:db8::5", "http://[2001:db8::5]:8000"),
        ("[::1]", "http://[::1]:8000"),
    ],
)
def test_browser_url_is_a_connect_address(host: str, expected: str) -> None:
    assert cli._browser_url(host, 8000) == expected


def test_serve_open_cancels_the_browser_when_the_server_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timer = FakeTimer()
    monkeypatch.setattr(cli, "_open_later", lambda url: timer)

    def fail(*a: object, **k: object) -> None:
        raise SystemExit(1)

    monkeypatch.setattr(cli.uvicorn, "run", fail)
    result = runner.invoke(app, ["serve", "--open", "--db", ":memory:"])
    assert result.exit_code == 1
    assert timer.cancelled


def test_serve_open_launches_the_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(cli, "_open_later", lambda url: (opened.append(url), FakeTimer())[1])
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **k: None)
    result = runner.invoke(app, ["serve", "--open", "--port", "8765", "--db", ":memory:"])
    assert result.exit_code == 0, result.output
    assert opened == ["http://127.0.0.1:8765"]


def test_serve_without_open_does_not_launch_the_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(cli, "_open_later", opened.append)
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **k: None)
    result = runner.invoke(app, ["serve", "--db", ":memory:"])
    assert result.exit_code == 0, result.output
    assert opened == []


@respx.mock
def test_crawl_prints_the_next_step(tmp_path: Path) -> None:
    respx.get("https://e.com/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://e.com/sitemap.xml").mock(return_value=httpx.Response(404))
    respx.get("https://e.com/").mock(return_value=httpx.Response(200, html="<p>x</p>"))
    args = ["crawl", "https://e.com/", "--no-ai", "--db", str(tmp_path / "t.db")]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    assert f"next: seo-scout serve --open --db {tmp_path / 't.db'}" in result.output


@respx.mock
def test_next_step_quotes_a_db_path_with_spaces(tmp_path: Path) -> None:
    respx.get("https://e.com/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://e.com/sitemap.xml").mock(return_value=httpx.Response(404))
    respx.get("https://e.com/").mock(return_value=httpx.Response(200, html="<p>x</p>"))
    db_file = tmp_path / "my sites" / "t.db"
    db_file.parent.mkdir()
    result = runner.invoke(app, ["crawl", "https://e.com/", "--no-ai", "--db", str(db_file)])
    assert result.exit_code == 0, result.output
    assert f'next: seo-scout serve --open --db "{db_file}"' in result.output


@respx.mock
def test_report_keeps_its_output_clean_for_schedulers(tmp_path: Path) -> None:
    respx.get("https://e.com/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://e.com/sitemap.xml").mock(return_value=httpx.Response(404))
    respx.get("https://e.com/").mock(return_value=httpx.Response(200, html="<p>x</p>"))
    db, out = str(tmp_path / "t.db"), str(tmp_path)
    args = ["report", "https://e.com/", "--no-ai", "--db", db, "--out", out]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    assert "next:" not in result.output  # stderr is not a terminal here, as under cron
