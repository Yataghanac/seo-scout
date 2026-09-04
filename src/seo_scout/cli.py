"""Typer CLI entry point. Wires config, logging, storage, and the crawler together."""

from __future__ import annotations

import asyncio
import ssl
from typing import Annotated, Any

import httpx
import truststore
import typer

from seo_scout import __version__
from seo_scout.audit.service import audit_run
from seo_scout.config import Settings
from seo_scout.crawler.crawler import Crawler
from seo_scout.logging import bind_run_id, configure_logging
from seo_scout.models import RunSummary
from seo_scout.store import db, repo_runs

app = typer.Typer(
    help="SEO Scout: crawl, audit, and propose validated title/meta rewrites.",
    no_args_is_help=True,
)


@app.command()
def version() -> None:
    """Print the installed version."""
    typer.echo(__version__)


@app.command()
def crawl(
    url: Annotated[str, typer.Argument(help="Site to crawl, e.g. https://example.com")],
    max_pages: Annotated[int | None, typer.Option(help="Stop after N pages (<= config)")] = None,
    max_depth: Annotated[int | None, typer.Option(help="Max link depth (<= config)")] = None,
    delay: Annotated[float | None, typer.Option(help="Min seconds between requests")] = None,
    db_path: Annotated[str | None, typer.Option("--db", help="SQLite file")] = None,
    dry_run: Annotated[bool, typer.Option(help="Print seed URLs and exit")] = False,
    verbose: Annotated[bool, typer.Option(help="Per-URL debug logs")] = False,
) -> None:
    """Crawl a site and store a numbered run."""
    overrides: dict[str, Any] = {}
    if delay is not None:
        overrides["min_delay"] = delay
    if db_path is not None:
        overrides["db"] = db_path
    settings = Settings(**overrides)
    configure_logging(verbose=verbose)
    typer.echo(settings.effective_config_line(), err=True)
    try:
        asyncio.run(_crawl(settings, url, max_pages, max_depth, dry_run))
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    except KeyboardInterrupt:
        typer.echo("interrupted: partial run saved", err=True)
        raise typer.Exit(code=130) from None


async def _crawl(
    settings: Settings, url: str, max_pages: int | None, max_depth: int | None, dry_run: bool
) -> None:
    conn = db.connect(settings.db)
    # Trust the OS certificate store (corporate proxies, local CAs) instead of certifi only.
    tls = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    async with httpx.AsyncClient(
        http2=False,
        headers={"user-agent": settings.user_agent},
        timeout=settings.request_timeout,
        verify=tls,
    ) as client:
        crawler = Crawler(settings=settings, conn=conn, client=client)
        if dry_run:
            for seed in await crawler.plan(url):
                typer.echo(seed)
            return
        report = await crawler.run(url, max_pages=max_pages, max_depth=max_depth)
    typer.echo(
        f"run {report.run_id}: {report.status}, {report.pages} pages in {report.elapsed_s:.1f}s"
        + (f" ({report.error})" if report.error else "")
    )
    if report.status != "failed":
        _echo_summary(audit_run(conn, report.run_id))


def _echo_summary(summary: RunSummary) -> None:
    issues = sum(summary.issues_by_rule.values())
    by_sev = ", ".join(f"{n} {sev}" for sev, n in sorted(summary.issues_by_severity.items()))
    typer.echo(
        f"audit: {summary.pages_audited} pages, average score {summary.average_score}, "
        f"{issues} issues ({by_sev or 'none'})"
    )


@app.command()
def audit(
    run_id: Annotated[int, typer.Argument(help="Run to (re-)audit")],
    db_path: Annotated[str | None, typer.Option("--db", help="SQLite file")] = None,
    verbose: Annotated[bool, typer.Option(help="Debug logs")] = False,
) -> None:
    """Re-run the deterministic audit rules over a stored crawl."""
    settings = Settings(db=db_path) if db_path else Settings()
    configure_logging(verbose=verbose)
    conn = db.connect(settings.db)
    if repo_runs.get_run(conn, run_id) is None:
        raise typer.BadParameter(f"run {run_id} does not exist in {settings.db}")
    bind_run_id(run_id)
    _echo_summary(audit_run(conn, run_id))


if __name__ == "__main__":
    app()
