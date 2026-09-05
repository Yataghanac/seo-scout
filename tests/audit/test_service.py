"""The I/O edge: load a run, audit it, persist issues and scores, summarise."""

import sqlite3
from datetime import UTC, datetime

from seo_scout.audit.service import audit_run, summarize_run
from seo_scout.models import FetchedPage
from seo_scout.store import repo_issues, repo_pages, repo_runs


def fetched(url: str, html: str | None, status: int = 200) -> FetchedPage:
    return FetchedPage(
        url=url,
        final_url=url,
        status=status,
        depth=0,
        content_type="text/html" if html is not None else None,
        bytes=len(html or ""),
        elapsed_ms=10,
        fetched_at=datetime.now(UTC),
        html=html,
    )


GOOD = (
    '<html lang="en"><head><title>Fresh Roasted Coffee Beans Delivered Weekly</title>'
    '<meta name="description" content="Order freshly roasted single-origin coffee beans, '
    'delivered to your door every week with free tasting notes and brewing guides.">'
    '<link rel="canonical" href="{url}"><meta property="og:title" content="t">'
    '<meta property="og:description" content="d"></head><body><h1>H</h1><p>{words}</p>'
    '<a href="/dead">dead</a></body></html>'
)


def seed(conn: sqlite3.Connection) -> int:
    run_id = repo_runs.create_run(conn, "https://e.com/", {})
    words = " ".join(f"w{i}" for i in range(320))
    repo_pages.insert_page(
        conn, run_id, fetched("https://e.com/", GOOD.format(url="https://e.com/", words=words))
    )
    repo_pages.insert_page(
        conn, run_id, fetched("https://e.com/bad", "<html><body>x</body></html>")
    )
    repo_pages.insert_page(conn, run_id, fetched("https://e.com/dead", None, status=404))
    repo_pages.insert_page(conn, run_id, fetched("https://e.com/skipped", None, status=200))
    repo_pages.insert_links(conn, run_id, "https://e.com/", ["https://e.com/dead"])
    repo_runs.finish_run(conn, run_id, "complete", pages=4)
    return run_id


def test_audit_persists_issues_and_scores(conn: sqlite3.Connection) -> None:
    run_id = seed(conn)
    summary = audit_run(conn, run_id)
    assert summary.pages_audited == 2  # the 404 and the body-less page are not audited
    scores = repo_issues.scores_by_url(conn, run_id)
    assert scores["https://e.com/bad"] == 100 - 15 - 5 - 5 - 5 - 2 - 2 - 2  # 64
    assert scores["https://e.com/"] == 100 - 15  # one critical: the broken link
    issues = repo_issues.list_issues(conn, run_id)
    assert {i.rule_id for i in issues if i.url == "https://e.com/"} == {"broken_links"}
    assert summary.issues_by_rule["broken_links"] == 1
    assert summary.issues_by_severity["critical"] >= 1


def test_audit_is_idempotent(conn: sqlite3.Connection) -> None:
    run_id = seed(conn)
    first = audit_run(conn, run_id)
    second = audit_run(conn, run_id)
    assert first == second
    assert len(repo_issues.list_issues(conn, run_id)) == sum(first.issues_by_rule.values())


def test_summary_shape(conn: sqlite3.Connection) -> None:
    run_id = seed(conn)
    audit_run(conn, run_id)
    summary = summarize_run(conn, run_id)
    assert summary.run_id == run_id
    assert summary.average_score == (85 + 64) / 2
    assert sum(summary.score_distribution.values()) == 2
    assert summary.score_distribution["80-100"] == 1
    assert summary.score_distribution["60-79"] == 1
    assert summary.worst_pages[0].url == "https://e.com/bad"
    assert summary.worst_pages[0].score == 64
    assert summary.worst_pages[0].issues > 0


def test_empty_run_summarises_without_dividing_by_zero(conn: sqlite3.Connection) -> None:
    run_id = repo_runs.create_run(conn, "https://e.com/", {})
    summary = audit_run(conn, run_id)
    assert summary.pages_audited == 0
    assert summary.average_score == 0
    assert summary.worst_pages == []


def test_worst_pages_never_lists_a_clean_page(conn: sqlite3.Connection) -> None:
    """Export, report and Slack all read worst_pages; a clean page is not a place to start."""
    run_id = repo_runs.create_run(conn, "https://e.com/", {})
    words = " ".join(f"w{i}" for i in range(320))
    clean = GOOD.replace('<a href="/dead">dead</a>', "").format(
        url="https://e.com/clean", words=words
    )
    repo_pages.insert_page(conn, run_id, fetched("https://e.com/clean", clean))
    repo_pages.insert_page(
        conn, run_id, fetched("https://e.com/bad", "<html><body>x</body></html>")
    )
    repo_runs.finish_run(conn, run_id, "complete", pages=2)
    summary = audit_run(conn, run_id)
    assert summary.pages_audited == 2
    assert [w.url for w in summary.worst_pages] == ["https://e.com/bad"]
    assert all(w.issues > 0 for w in summary.worst_pages)
