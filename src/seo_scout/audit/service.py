"""The I/O edge of the audit layer: load a run, audit it, persist, summarise."""

from __future__ import annotations

import logging
import sqlite3
from collections import Counter

from seo_scout.audit.engine import audit_pages, build_context
from seo_scout.audit.page import AuditPage
from seo_scout.audit.score import bucket, score_for
from seo_scout.models import PageIssue, RunSummary, WorstPage
from seo_scout.store import repo_issues, repo_pages

log = logging.getLogger("seo_scout.audit")

BUCKETS = ("0-19", "20-39", "40-59", "60-79", "80-100")
WORST_PAGES = 10


def audit_run(conn: sqlite3.Connection, run_id: int) -> RunSummary:
    """Re-auditing a run replaces its issues and scores; the result is deterministic."""
    fetched = repo_pages.list_pages(conn, run_id)
    pages = [p for p in (AuditPage.from_fetched(f) for f in fetched) if p is not None]
    ctx = build_context(
        pages,
        inbound=repo_pages.inbound_counts(conn, run_id),
        sitemap_urls=repo_pages.sitemap_urls(conn, run_id),
        status_by_url=repo_pages.status_by_url(conn, run_id),
    )
    results = audit_pages(pages, ctx)
    issues = [
        PageIssue(url=url, rule_id=i.rule_id, severity=i.severity, message=i.message)
        for url, found in results.items()
        for i in found
    ]
    scores = {url: score_for(found) for url, found in results.items()}
    repo_issues.replace_results(conn, run_id, issues, scores)
    log.info("audit finished", extra={"pages": len(pages), "issues": len(issues)})
    return summarize_run(conn, run_id)


def summarize_run(conn: sqlite3.Connection, run_id: int) -> RunSummary:
    """`worst_pages` lists only pages with issues: it is a to-do list, not a ranking."""
    scores = repo_issues.scores_by_url(conn, run_id)
    issues = repo_issues.list_issues(conn, run_id)
    per_page: Counter[str] = Counter(i.url for i in issues)
    distribution = {b: 0 for b in BUCKETS}
    for score in scores.values():
        distribution[bucket(score)] += 1
    flawed = [(url, score) for url, score in scores.items() if per_page[url]]
    worst = sorted(flawed, key=lambda kv: (kv[1], -per_page[kv[0]], kv[0]))
    return RunSummary(
        run_id=run_id,
        pages_audited=len(scores),
        average_score=round(sum(scores.values()) / len(scores), 1) if scores else 0.0,
        score_distribution=distribution,
        issues_by_rule=dict(Counter(i.rule_id for i in issues)),
        issues_by_severity=dict(Counter(i.severity.value for i in issues)),
        worst_pages=[
            WorstPage(url=url, score=score, issues=per_page[url])
            for url, score in worst[:WORST_PAGES]
        ],
    )
