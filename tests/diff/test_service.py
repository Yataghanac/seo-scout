"""Loading snapshots from the store and finding the previous run of a site."""

import sqlite3
from datetime import UTC, datetime

from seo_scout.audit.service import audit_run
from seo_scout.diff.service import diff_run_ids, snapshot
from seo_scout.models import FetchedPage
from seo_scout.store import repo_pages, repo_runs


def page(url: str, html: str) -> FetchedPage:
    return FetchedPage(
        url=url,
        final_url=url,
        status=200,
        depth=0,
        content_type="text/html",
        bytes=len(html),
        elapsed_ms=1,
        fetched_at=datetime.now(UTC),
        html=html,
    )


def seed(
    conn: sqlite3.Connection, start: str, pages: dict[str, str], status: str = "complete"
) -> int:
    run_id = repo_runs.create_run(conn, start, {})
    for url, html in pages.items():
        repo_pages.insert_page(conn, run_id, page(url, html))
    repo_runs.finish_run(conn, run_id, status, pages=len(pages))
    audit_run(conn, run_id)
    return run_id


def test_snapshot_carries_scores_and_rule_sets(conn: sqlite3.Connection) -> None:
    run_id = seed(conn, "https://e.com/", {"https://e.com/": "<html><body>x</body></html>"})
    snap = snapshot(conn, run_id)
    assert snap.run_id == run_id
    assert snap.pages["https://e.com/"].score == 64
    assert "title_missing" in snap.pages["https://e.com/"].rules


def test_diff_run_ids_reflects_a_fixed_title(conn: sqlite3.Connection) -> None:
    a = seed(conn, "https://e.com/", {"https://e.com/": "<html><body>x</body></html>"})
    b = seed(
        conn,
        "https://e.com/",
        {
            "https://e.com/": (
                "<html><head><title>A Proper Title Of Reasonable Length</title></head>"
                "<body>x</body></html>"
            )
        },
    )
    d = diff_run_ids(conn, a, b)
    assert [(i.url, i.rule_id) for i in d.issues_fixed] == [("https://e.com/", "title_missing")]
    assert d.score_changes[0].delta == 15


def test_previous_run_matches_site_and_skips_failed_runs(conn: sqlite3.Connection) -> None:
    first = seed(conn, "https://e.com/", {})
    seed(conn, "https://other.test/", {})
    failed = repo_runs.create_run(conn, "https://e.com/", {})
    repo_runs.finish_run(conn, failed, "failed", pages=0, error="robots")
    latest = seed(conn, "https://e.com/", {})
    previous = repo_runs.previous_run(conn, latest)
    assert previous is not None and previous.id == first
    assert repo_runs.previous_run(conn, first) is None
