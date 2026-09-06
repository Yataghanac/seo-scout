"""Join pages, scores, issues and AI rows into PageView objects; filter and sort them.

A run holds at most `Settings.max_pages` rows (5,000 at the ceiling), so filtering and
sorting happen in Python on one query's worth of rows rather than in SQL. That keeps the
repository layer free of API concerns.
"""

from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from typing import Literal

from seo_scout.api.schemas import AISummary, PageView
from seo_scout.models import Issue, Severity
from seo_scout.store import repo_ai, repo_issues, repo_pages

SortKey = Literal["score", "url", "issues", "status"]
Order = Literal["asc", "desc"]


def load_pages(conn: sqlite3.Connection, run_id: int) -> list[PageView]:
    scores = repo_issues.scores_by_url(conn, run_id)
    issues: dict[str, list[Issue]] = defaultdict(list)
    for found in repo_issues.list_issues(conn, run_id):
        issues[found.url].append(
            Issue(rule_id=found.rule_id, severity=found.severity, message=found.message)
        )
    ai = {row.url: row for row in repo_ai.list_suggestions(conn, run_id)}
    views = []
    for page in repo_pages.list_pages(conn, run_id, with_html=False):
        if page.url not in scores:
            continue  # not auditable: no HTML body
        page_issues = issues.get(page.url, [])
        counts = Counter(i.severity.value for i in page_issues)
        views.append(
            PageView(
                url=page.url,
                final_url=page.final_url,
                status=page.status,
                depth=page.depth,
                bytes=page.bytes,
                elapsed_ms=page.elapsed_ms,
                score=scores[page.url],
                counts={s.value: counts.get(s.value, 0) for s in Severity},
                issues=page_issues,
                ai=ai.get(page.url),
            )
        )
    return views


def ai_summary(conn: sqlite3.Connection, run_id: int) -> AISummary:
    counts = Counter(row.status for row in repo_ai.list_suggestions(conn, run_id))
    cached = sum(1 for row in repo_ai.list_suggestions(conn, run_id) if row.cached)
    calls, prompt_tokens, completion_tokens, usd = repo_ai.run_cost(conn, run_id)
    return AISummary(
        ok=counts.get("ok", 0),
        repaired=counts.get("repaired", 0),
        rejected=counts.get("rejected", 0),
        skipped=counts.get("skipped", 0),
        unavailable=counts.get("unavailable", 0),
        cached=cached,
        calls=calls,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        usd=usd,
    )


def select(
    pages: list[PageView],
    *,
    severity: Severity | None,
    rule: str | None,
    sort: SortKey,
    order: Order,
) -> list[PageView]:
    kept = [
        p
        for p in pages
        if (severity is None or p.counts[severity.value] > 0)
        and (rule is None or any(i.rule_id == rule for i in p.issues))
    ]
    keys = {
        "score": lambda p: p.score,
        "url": lambda p: p.url,
        "issues": lambda p: len(p.issues),
        "status": lambda p: p.status,
    }
    return sorted(kept, key=keys[sort], reverse=order == "desc")
