"""Response shapes for the dashboard API."""

from __future__ import annotations

from pydantic import BaseModel

from seo_scout.models import Issue, Run, RunSummary, Severity, SuggestionRow


class RuleInfo(BaseModel):
    severity: Severity
    explanation: str


class AISummary(BaseModel):
    ok: int = 0
    repaired: int = 0
    rejected: int = 0
    skipped: int = 0
    unavailable: int = 0
    cached: int = 0
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    usd: float = 0.0


class SummaryResponse(BaseModel):
    run: Run
    summary: RunSummary
    ai: AISummary
    rules: dict[str, RuleInfo]


class PageView(BaseModel):
    url: str
    final_url: str
    status: int
    depth: int
    bytes: int
    elapsed_ms: int
    score: int
    counts: dict[str, int]  # critical / warning / notice
    issues: list[Issue]
    ai: SuggestionRow | None


class PagesResponse(BaseModel):
    items: list[PageView]
    total: int
    page: int
    size: int


class ExportResponse(BaseModel):
    run: Run
    summary: RunSummary
    ai: AISummary
    pages: list[PageView]
