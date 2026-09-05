"""Typer CLI entry point. Wires config, logging, storage, crawler, audit, AI, API, diff."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import ssl
import sys
import threading
import webbrowser
from contextlib import closing
from importlib import resources
from pathlib import Path
from typing import Annotated, Any

import httpx
import truststore
import typer
import uvicorn

from seo_scout import __version__
from seo_scout.ai.client import make_completer
from seo_scout.ai.pipeline import AIRunReport, enrich_run
from seo_scout.api.app import create_app
from seo_scout.audit.service import audit_run, summarize_run
from seo_scout.config import Settings
from seo_scout.crawler.crawler import Crawler
from seo_scout.diff.differ import RunDiff
from seo_scout.diff.notify import post_slack, slack_summary
from seo_scout.diff.report import to_json, to_markdown, to_table
from seo_scout.diff.service import diff_run_ids
from seo_scout.logging import bind_run_id, configure_logging
from seo_scout.models import RunSummary
from seo_scout.store import db, repo_runs

app = typer.Typer(
    help="SEO Scout: crawl, audit, and propose validated title/meta rewrites.",
    no_args_is_help=True,
)

DbOpt = Annotated[str | None, typer.Option("--db", help="SQLite file")]
VerboseOpt = Annotated[bool, typer.Option(help="Per-URL debug logs")]


@app.command()
def version() -> None:
    """Print the installed version."""
    typer.echo(__version__)


@app.command()
def init() -> None:
    """Write .env from the packaged template so the next step is just `crawl`."""
    env = Path(".env")
    if env.exists():
        typer.echo(f"{env} already exists; edit it to change settings.")
    else:
        env.write_text(env_template(), encoding="utf-8")
        typer.echo(f"wrote {env}")
    typer.echo("optional: set OPENAI_API_KEY in .env to enable validated title/meta rewrites")
    typer.echo("next: seo-scout crawl https://example.com --max-pages 50")


@app.command()
def crawl(
    url: Annotated[str, typer.Argument(help="Site to crawl, e.g. https://example.com")],
    max_pages: Annotated[int | None, typer.Option(help="Stop after N pages (<= config)")] = None,
    max_depth: Annotated[int | None, typer.Option(help="Max link depth (<= config)")] = None,
    delay: Annotated[float | None, typer.Option(help="Min seconds between requests")] = None,
    db_path: DbOpt = None,
    dry_run: Annotated[bool, typer.Option(help="Print seed URLs and exit")] = False,
    no_ai: Annotated[bool, typer.Option("--no-ai", help="Skip the AI stage")] = False,
    max_cost: Annotated[
        float | None, typer.Option(help="Abort AI stage before exceeding USD")
    ] = None,
    ai_concurrency: Annotated[int | None, typer.Option(help="Parallel model calls")] = None,
    verbose: VerboseOpt = False,
) -> None:
    """Crawl a site, audit it, and propose validated title/meta rewrites."""
    settings = _setup(_settings(delay, db_path, max_cost, ai_concurrency), verbose)
    run_id = _run(_crawl(settings, url, max_pages, max_depth, dry_run, no_ai))
    if run_id is not None:
        _next_step(db_path)


@app.command()
def audit(run_id: int, db_path: DbOpt = None, verbose: VerboseOpt = False) -> None:
    """Re-run the deterministic audit rules over a stored crawl."""
    settings = _setup(_settings(None, db_path, None, None), verbose)
    with closing(_open_run(settings, run_id)) as conn:
        _echo_summary(audit_run(conn, run_id))


@app.command()
def ai(
    run_id: int,
    db_path: DbOpt = None,
    max_cost: Annotated[float | None, typer.Option(help="Abort before exceeding USD")] = None,
    verbose: VerboseOpt = False,
) -> None:
    """Run (or re-run) the AI title/meta stage over a stored, audited run."""
    settings = _setup(_settings(None, db_path, max_cost, None), verbose)
    with closing(_open_run(settings, run_id)) as conn:
        audit_run(conn, run_id)
        asyncio.run(_enrich(conn, settings, run_id))


@app.command()
def serve(
    port: Annotated[int, typer.Option(help="Port for the dashboard")] = 8000,
    host: Annotated[str, typer.Option(help="Bind address")] = "127.0.0.1",
    db_path: DbOpt = None,
    open_browser: Annotated[bool, typer.Option("--open", help="Open the dashboard")] = False,
) -> None:
    """Serve the dashboard and JSON API."""
    settings = _settings(None, db_path, None, None)
    url = _browser_url(host, port)
    typer.echo(f"dashboard: {url}  (db: {settings.db})")
    timer = _open_later(url) if open_browser else None
    try:
        uvicorn.run(create_app(settings.db), host=host, port=port, log_level="warning")
    finally:
        if timer is not None:
            timer.cancel()  # bind failed or Ctrl+C: do not open a tab on someone else's port


@app.command()
def diff(
    run_a: int,
    run_b: int,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output")] = False,
    db_path: DbOpt = None,
) -> None:
    """Compare two runs: pages added/removed, score changes, issues fixed/introduced."""
    settings = _settings(None, db_path, None, None)
    with closing(db.connect(settings.db)) as conn:
        for run_id in (run_a, run_b):
            if repo_runs.get_run(conn, run_id) is None:
                raise typer.BadParameter(f"run {run_id} does not exist in {settings.db}")
        result = diff_run_ids(conn, run_a, run_b)
    typer.echo(to_json(result) if as_json else to_table(result))


@app.command()
def report(
    url: Annotated[str, typer.Argument(help="Site to crawl and compare with its last run")],
    max_pages: Annotated[int | None, typer.Option(help="Stop after N pages (<= config)")] = None,
    max_depth: Annotated[int | None, typer.Option(help="Max link depth (<= config)")] = None,
    delay: Annotated[float | None, typer.Option(help="Min seconds between requests")] = None,
    db_path: DbOpt = None,
    no_ai: Annotated[bool, typer.Option("--no-ai", help="Skip the AI stage")] = False,
    max_cost: Annotated[
        float | None, typer.Option(help="Abort AI stage before exceeding USD")
    ] = None,
    out: Annotated[Path, typer.Option(help="Directory for report files")] = Path("reports"),
    verbose: VerboseOpt = False,
) -> None:
    """Crawl, diff against the previous run of the same site, write a report, notify Slack.

    Built for schedulers: one command, exit code 0, files on disk, optional webhook.
    """
    settings = _setup(_settings(delay, db_path, max_cost, None), verbose)
    run_id, body, result = _run(_report(settings, url, max_pages, max_depth, no_ai))
    out.mkdir(parents=True, exist_ok=True)
    (out / f"run-{run_id}.json").write_text(json.dumps(body, indent=2), encoding="utf-8")
    if result is None:
        text = f"# Run {run_id} baseline\n\nNo previous run of {url} to compare with.\n"
        typer.echo(f"baseline: no previous run of {url}; wrote {out / f'run-{run_id}.md'}")
    else:
        text = to_markdown(result)
        typer.echo(to_table(result))
    (out / f"run-{run_id}.md").write_text(text, encoding="utf-8")
    if result is not None and settings.slack_webhook_url:
        posted = _run(post_slack(settings.slack_webhook_url, slack_summary(result, url)))
        typer.echo("slack: posted" if posted else "slack: failed (see logs)", err=not posted)
    if sys.stderr.isatty():  # a person ran it; keep cron mail and CI logs clean
        _next_step(db_path)


# --- helpers ---------------------------------------------------------------------------------


def env_template() -> str:
    """The `.env` template shipped inside the package (a copy of the repo's .env.example)."""
    return resources.files("seo_scout").joinpath("env.example").read_text(encoding="utf-8")


def _next_step(db_path: str | None) -> None:
    hint = "next: seo-scout serve --open" + (f" --db {db_path}" if db_path else "")
    typer.echo(hint, err=True)


def _browser_url(host: str, port: int) -> str:
    """A URL a browser can open: wildcard binds become loopback, IPv6 gets brackets."""
    if host in ("0.0.0.0", ""):
        host = "127.0.0.1"
    elif host == "::":
        host = "::1"
    if ":" in host:
        host = f"[{host}]"
    return f"http://{host}:{port}"


def _open_later(url: str, delay_s: float = 0.8) -> threading.Timer:
    """Open the browser once uvicorn has had a moment to bind. Cancel it if serving fails."""
    timer = threading.Timer(delay_s, webbrowser.open, [url])
    timer.daemon = True
    timer.start()
    return timer


def _settings(
    delay: float | None, db_path: str | None, max_cost: float | None, ai_concurrency: int | None
) -> Settings:
    overrides: dict[str, Any] = {}
    if delay is not None:
        overrides["min_delay"] = delay
    if db_path is not None:
        overrides["db"] = db_path
    if max_cost is not None:
        overrides["max_cost_usd"] = max_cost
    if ai_concurrency is not None:
        overrides["ai_concurrency"] = ai_concurrency
    return Settings(**overrides)


def _setup(settings: Settings, verbose: bool) -> Settings:
    """Logging, the effective-config line, and the OS trust store for every TLS client."""
    configure_logging(verbose=verbose)
    typer.echo(settings.effective_config_line(), err=True)
    truststore.inject_into_ssl()
    return settings


def _run(coro: Any) -> Any:
    try:
        return asyncio.run(coro)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    except KeyboardInterrupt:
        typer.echo("interrupted: partial run saved", err=True)
        raise typer.Exit(code=130) from None


def _client(settings: Settings) -> httpx.AsyncClient:
    tls = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    return httpx.AsyncClient(
        http2=False,
        headers={"user-agent": settings.user_agent},
        timeout=settings.request_timeout,
        verify=tls,
    )


async def _crawl(
    settings: Settings,
    url: str,
    max_pages: int | None,
    max_depth: int | None,
    dry_run: bool,
    no_ai: bool,
) -> int | None:
    """Crawl, audit, enrich. Returns the run id, or None for a dry run or a failed crawl."""
    with closing(db.connect(settings.db)) as conn:
        async with _client(settings) as client:
            crawler = Crawler(settings=settings, conn=conn, client=client)
            if dry_run:
                for seed in await crawler.plan(url):
                    typer.echo(seed)
                return None
            result = await crawler.run(url, max_pages=max_pages, max_depth=max_depth)
        typer.echo(
            f"run {result.run_id}: {result.status}, {result.pages} pages in "
            f"{result.elapsed_s:.1f}s" + (f" ({result.error})" if result.error else "")
        )
        if result.status == "failed":
            return None
        _echo_summary(audit_run(conn, result.run_id))
        if not no_ai:
            await _enrich(conn, settings, result.run_id)
        return result.run_id


async def _report(
    settings: Settings, url: str, max_pages: int | None, max_depth: int | None, no_ai: bool
) -> tuple[int, dict[str, Any], RunDiff | None]:
    """Crawl, then compare with the previous run of the same site. Returns data, writes nothing."""
    run_id = await _crawl(settings, url, max_pages, max_depth, False, no_ai)
    if run_id is None:
        raise typer.Exit(code=1)
    with closing(db.connect(settings.db)) as conn:
        run = repo_runs.get_run(conn, run_id)
        previous = repo_runs.previous_run(conn, run_id)
        summary = summarize_run(conn, run_id)
        result = diff_run_ids(conn, previous.id, run_id) if previous else None
    body = {
        "run": run.model_dump(mode="json") if run else None,
        "summary": summary.model_dump(mode="json"),
        "diff": result.model_dump(mode="json") if result else None,
    }
    return run_id, body, result


async def _enrich(conn: sqlite3.Connection, settings: Settings, run_id: int) -> None:
    result = await enrich_run(conn, run_id, settings, make_completer(settings))
    _echo_ai(result)


def _echo_summary(summary: RunSummary) -> None:
    issues = sum(summary.issues_by_rule.values())
    by_sev = ", ".join(f"{n} {sev}" for sev, n in sorted(summary.issues_by_severity.items()))
    typer.echo(
        f"audit: {summary.pages_audited} pages, average score {summary.average_score}, "
        f"{issues} issues ({by_sev or 'none'})"
    )


def _echo_ai(r: AIRunReport) -> None:
    typer.echo(
        f"ai: {r.considered} pages considered, {r.ok} ok, {r.repaired} repaired, "
        f"{r.rejected} rejected, {r.cached} cached, {r.skipped} skipped; "
        f"{r.calls} calls, ${r.cost_usd:.4f}"
    )
    if r.warning:
        typer.echo(f"warning: {r.warning}", err=True)


def _open_run(settings: Settings, run_id: int) -> sqlite3.Connection:
    conn = db.connect(settings.db)
    if repo_runs.get_run(conn, run_id) is None:
        conn.close()
        raise typer.BadParameter(f"run {run_id} does not exist in {settings.db}")
    bind_run_id(run_id)
    return conn


if __name__ == "__main__":
    app()
