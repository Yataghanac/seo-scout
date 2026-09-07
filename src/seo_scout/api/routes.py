"""Dashboard API routes. One SQLite connection per request, closed on exit."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Annotated, Protocol
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from seo_scout.api import targets
from seo_scout.api.assemble import Order, SortKey, ai_summary, load_pages, select
from seo_scout.api.export import csv_rows
from seo_scout.api.schemas import (
    ExportResponse,
    PagesResponse,
    RuleInfo,
    SummaryResponse,
)
from seo_scout.audit.registry import RULES
from seo_scout.audit.service import summarize_run
from seo_scout.models import Run, Severity
from seo_scout.store import db, repo_runs
from seo_scout.urls import link_pair

router = APIRouter(prefix="/api")


def get_conn(request: Request) -> Iterator[sqlite3.Connection]:
    conn = db.connect(request.app.state.db_path)
    try:
        yield conn
    finally:
        conn.close()


Conn = Annotated[sqlite3.Connection, Depends(get_conn)]


class CrawlRunner(Protocol):
    """What the API needs from whoever can crawl. `api` never imports `crawler`."""

    def start(self, url: str, max_pages: int | None) -> bool:
        """Begin a crawl in the background. False when one is already running."""


class CrawlRequest(BaseModel):
    url: str
    max_pages: int | None = None


def _run_or_404(conn: sqlite3.Connection, run_id: int) -> Run:
    run = repo_runs.get_run(conn, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    return run


@router.post("/crawls", status_code=202)
def start_crawl(body: CrawlRequest, request: Request) -> dict[str, str]:
    """Accept a crawl. The run id is not returned: the dashboard polls /api/runs for it."""
    runner: CrawlRunner | None = request.app.state.crawl_runner
    if runner is None:
        raise HTTPException(status_code=501, detail="this deployment cannot start crawls")
    link = link_pair(body.url)
    if link is None:
        raise HTTPException(status_code=400, detail="that is not an http(s) URL")
    allowlist = request.app.state.allowlist
    if reason := targets.check_url(link.url, allowlist):
        raise HTTPException(status_code=400, detail=reason)
    if reason := targets.resolve_reason(urlsplit(link.url).hostname or ""):
        raise HTTPException(status_code=400, detail=reason)
    if not runner.start(link.url, body.max_pages):
        raise HTTPException(status_code=409, detail="a crawl is already running")
    return {"status": "started"}


@router.get("/runs")
def list_runs(conn: Conn) -> list[Run]:
    return repo_runs.list_runs(conn)


@router.get("/runs/{run_id}/summary")
def run_summary(run_id: int, conn: Conn) -> SummaryResponse:
    run = _run_or_404(conn, run_id)
    return SummaryResponse(
        run=run,
        summary=summarize_run(conn, run_id),
        ai=ai_summary(conn, run_id),
        rules={r.id: RuleInfo(severity=r.severity, explanation=r.explanation) for r in RULES},
    )


@router.get("/runs/{run_id}/pages")
def run_pages(
    run_id: int,
    conn: Conn,
    severity: Severity | None = None,
    rule: str | None = None,
    sort: SortKey = "score",
    order: Order = "asc",
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=500)] = 50,
) -> PagesResponse:
    _run_or_404(conn, run_id)
    pages = select(load_pages(conn, run_id), severity=severity, rule=rule, sort=sort, order=order)
    start = (page - 1) * size
    return PagesResponse(items=pages[start : start + size], total=len(pages), page=page, size=size)


@router.get("/runs/{run_id}/export.csv")
def export_csv(run_id: int, conn: Conn) -> StreamingResponse:
    _run_or_404(conn, run_id)
    pages = select(load_pages(conn, run_id), severity=None, rule=None, sort="url", order="asc")
    return StreamingResponse(
        csv_rows(pages),
        media_type="text/csv; charset=utf-8",
        headers={"content-disposition": f'attachment; filename="seo-scout-run-{run_id}.csv"'},
    )


@router.get("/runs/{run_id}/export.json")
def export_json(run_id: int, conn: Conn) -> JSONResponse:
    run = _run_or_404(conn, run_id)
    body = ExportResponse(
        run=run,
        summary=summarize_run(conn, run_id),
        ai=ai_summary(conn, run_id),
        pages=select(load_pages(conn, run_id), severity=None, rule=None, sort="url", order="asc"),
    )
    return JSONResponse(
        content=body.model_dump(mode="json"),
        headers={"content-disposition": f'attachment; filename="seo-scout-run-{run_id}.json"'},
    )
