"""Repository for `issues` and `page_scores`."""

from __future__ import annotations

import sqlite3

from seo_scout.models import PageIssue, Severity


def replace_results(
    conn: sqlite3.Connection, run_id: int, issues: list[PageIssue], scores: dict[str, int]
) -> None:
    """Atomically replace a run's audit output."""
    with conn:
        conn.execute("DELETE FROM issues WHERE run_id = ?", (run_id,))
        conn.execute("DELETE FROM page_scores WHERE run_id = ?", (run_id,))
        conn.executemany(
            "INSERT INTO issues (run_id, url, rule_id, severity, message) VALUES (?, ?, ?, ?, ?)",
            [(run_id, i.url, i.rule_id, i.severity.value, i.message) for i in issues],
        )
        conn.executemany(
            "INSERT INTO page_scores (run_id, url, score) VALUES (?, ?, ?)",
            [(run_id, url, score) for url, score in scores.items()],
        )


def list_issues(conn: sqlite3.Connection, run_id: int) -> list[PageIssue]:
    rows = conn.execute(
        "SELECT url, rule_id, severity, message FROM issues WHERE run_id = ? ORDER BY id",
        (run_id,),
    ).fetchall()
    return [
        PageIssue(
            url=r["url"],
            rule_id=r["rule_id"],
            severity=Severity(r["severity"]),
            message=r["message"],
        )
        for r in rows
    ]


def scores_by_url(conn: sqlite3.Connection, run_id: int) -> dict[str, int]:
    rows = conn.execute("SELECT url, score FROM page_scores WHERE run_id = ?", (run_id,)).fetchall()
    return {r["url"]: r["score"] for r in rows}
