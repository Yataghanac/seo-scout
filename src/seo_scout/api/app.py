"""FastAPI application factory: the JSON API plus the single-file dashboard."""

from __future__ import annotations

from collections.abc import Sequence
from importlib import resources

from fastapi import Depends, FastAPI
from fastapi.responses import HTMLResponse

from seo_scout import __version__
from seo_scout.api.auth import login_router, token_guard
from seo_scout.api.routes import CrawlRunner, router


def create_app(
    db_path: str,
    *,
    crawl_runner: CrawlRunner | None = None,
    token: str | None = None,
    allowlist: Sequence[str] = (),
) -> FastAPI:
    app = FastAPI(title="SEO Scout", version=__version__, docs_url="/api/docs", redoc_url=None)
    app.state.db_path = db_path
    app.state.crawl_runner = crawl_runner
    app.state.token = token
    app.state.allowlist = list(allowlist)
    app.include_router(login_router)
    app.include_router(router, dependencies=[Depends(token_guard)])
    index = resources.files("seo_scout.api").joinpath("static/index.html")

    @app.get("/", include_in_schema=False)
    def dashboard() -> HTMLResponse:
        return HTMLResponse(index.read_text(encoding="utf-8"))

    return app
