"""FastAPI application factory: the JSON API plus the single-file dashboard."""

from __future__ import annotations

from importlib import resources

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from seo_scout import __version__
from seo_scout.api.routes import router


def create_app(db_path: str) -> FastAPI:
    app = FastAPI(title="SEO Scout", version=__version__, docs_url="/api/docs", redoc_url=None)
    app.state.db_path = db_path
    app.include_router(router)
    index = resources.files("seo_scout.api").joinpath("static/index.html")

    @app.get("/", include_in_schema=False)
    def dashboard() -> HTMLResponse:
        return HTMLResponse(index.read_text(encoding="utf-8"))

    return app
