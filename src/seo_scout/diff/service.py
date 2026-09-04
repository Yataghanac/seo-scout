"""I/O edge of the diff layer: build snapshots from the store."""

from __future__ import annotations

import sqlite3
from collections import defaultdict

from seo_scout.diff.differ import PageSnapshot, RunDiff, RunSnapshot, diff_runs
from seo_scout.store import repo_issues


def snapshot(conn: sqlite3.Connection, run_id: int) -> RunSnapshot:
    rules: dict[str, set[str]] = defaultdict(set)
    for issue in repo_issues.list_issues(conn, run_id):
        rules[issue.url].add(issue.rule_id)
    pages = {
        url: PageSnapshot(score=score, rules=rules.get(url, set()))
        for url, score in repo_issues.scores_by_url(conn, run_id).items()
    }
    return RunSnapshot(run_id=run_id, pages=pages)


def diff_run_ids(conn: sqlite3.Connection, run_a: int, run_b: int) -> RunDiff:
    return diff_runs(snapshot(conn, run_a), snapshot(conn, run_b))
