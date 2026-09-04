"""Shared pydantic models. Pure data; no I/O."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict


class Severity(StrEnum):
    critical = "critical"
    warning = "warning"
    notice = "notice"

    @property
    def weight(self) -> int:
        """Points subtracted from a page's 100-point score per issue."""
        return {"critical": 15, "warning": 5, "notice": 2}[self.value]


class Issue(BaseModel):
    """One finding from one rule on one page."""

    model_config = ConfigDict(frozen=True)

    rule_id: str
    severity: Severity
    message: str


class RedirectHop(BaseModel):
    url: str
    status: int


class FetchedPage(BaseModel):
    """One crawled URL exactly as persisted in the `pages` table."""

    url: str
    final_url: str
    status: int  # 0 means the request never completed (see `error`)
    depth: int
    content_type: str | None
    bytes: int
    elapsed_ms: int
    fetched_at: datetime
    html: str | None
    redirect_chain: list[RedirectHop] = []
    headers: dict[str, str] = {}
    error: str | None = None


class Run(BaseModel):
    """One crawl. `status` is running | complete | partial | failed."""

    id: int
    start_url: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    pages: int
    error: str | None
    settings: dict[str, Any]
