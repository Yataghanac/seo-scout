"""FastAPI application factory: the JSON API plus the single-file dashboard."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from importlib import resources

from fastapi import Depends, FastAPI
from fastapi.responses import HTMLResponse

from seo_scout import __version__
from seo_scout.api.auth import login_router, token_guard
from seo_scout.api.routes import CrawlRunner, router
from seo_scout.store import db, repo_runs


def create_app(
    db_path: str,
    *,
    crawl_runner: CrawlRunner | None = None,
    token: str | None = None,
    allowlist: Sequence[str] = (),
    wall_clock_seconds: int = 1800,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # A run left `running` by a killed process would otherwise poison `previous_run`'s
        # choice of diff baseline forever. Only rows older than the wall-clock ceiling are
        # touched, so a CLI crawl in progress against the same database is left alone.
        conn = db.connect(app.state.db_path)
        try:
            repo_runs.reconcile_interrupted(conn, older_than_seconds=app.state.wall_clock_seconds)
        finally:
            conn.close()
        yield

    app = FastAPI(
        title="SEO Scout",
        version=__version__,
        docs_url="/api/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.db_path = db_path
    app.state.crawl_runner = crawl_runner
    app.state.token = token
    app.state.allowlist = list(allowlist)
    app.state.wall_clock_seconds = wall_clock_seconds
    app.include_router(login_router)
    app.include_router(router, dependencies=[Depends(token_guard)])
    index = resources.files("seo_scout.api").joinpath("static/index.html")

    @app.get("/", include_in_schema=False)
    def dashboard() -> HTMLResponse:
        return HTMLResponse(index.read_text(encoding="utf-8"))

    return app
