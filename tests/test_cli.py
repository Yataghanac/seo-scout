import httpx
import respx
from typer.testing import CliRunner

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
    runs = repo_runs.list_runs(db.connect("t.db"))
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
