import sqlite3
from datetime import UTC, datetime, timedelta

from seo_scout.models import FetchedPage, RedirectHop
from seo_scout.store import db, repo_pages, repo_runs


def page(url: str, status: int = 200, depth: int = 0) -> FetchedPage:
    return FetchedPage(
        url=url,
        final_url=url,
        status=status,
        depth=depth,
        content_type="text/html",
        bytes=10,
        elapsed_ms=5,
        fetched_at=datetime(2026, 9, 4, tzinfo=UTC),
        html="<p>x</p>",
        redirect_chain=[RedirectHop(url=url, status=301)],
        headers={"x-robots-tag": "none"},
    )


def test_schema_is_idempotent(conn: sqlite3.Connection) -> None:
    db.init_schema(conn)
    db.init_schema(conn)
    tables = {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
    assert {"runs", "pages", "links"} <= tables


def test_run_lifecycle(conn: sqlite3.Connection) -> None:
    run_id = repo_runs.create_run(conn, "https://e.com/", {"max_pages": 5})
    assert run_id == 1
    run = repo_runs.get_run(conn, run_id)
    assert run is not None
    assert run.status == "running"
    assert run.start_url == "https://e.com/"
    repo_runs.finish_run(conn, run_id, "complete", pages=3)
    run = repo_runs.get_run(conn, run_id)
    assert run is not None
    assert run.status == "complete"
    assert run.pages == 3
    assert run.finished_at is not None
    assert [r.id for r in repo_runs.list_runs(conn)] == [1]


def test_missing_run_is_none(conn: sqlite3.Connection) -> None:
    assert repo_runs.get_run(conn, 99) is None


def test_pages_roundtrip_and_dedupe(conn: sqlite3.Connection) -> None:
    run_id = repo_runs.create_run(conn, "https://e.com/", {})
    repo_pages.insert_page(conn, run_id, page("https://e.com/a"))
    repo_pages.insert_page(conn, run_id, page("https://e.com/a", status=500))
    rows = repo_pages.list_pages(conn, run_id)
    assert len(rows) == 1
    got = rows[0]
    assert got.status == 200
    assert got.redirect_chain[0].status == 301
    assert got.headers["x-robots-tag"] == "none"
    assert got.fetched_at.tzinfo is not None


def test_links_and_status_lookup(conn: sqlite3.Connection) -> None:
    run_id = repo_runs.create_run(conn, "https://e.com/", {})
    repo_pages.insert_page(conn, run_id, page("https://e.com/"))
    repo_pages.insert_page(conn, run_id, page("https://e.com/dead", status=404))
    repo_pages.insert_links(
        conn, run_id, "https://e.com/", ["https://e.com/dead", "https://e.com/x"]
    )
    repo_pages.insert_links(conn, run_id, "https://e.com/", ["https://e.com/dead"])
    assert repo_pages.inbound_counts(conn, run_id) == {
        "https://e.com/dead": 1,
        "https://e.com/x": 1,
    }
    assert repo_pages.status_by_url(conn, run_id) == {
        "https://e.com/": 200,
        "https://e.com/dead": 404,
    }
    assert repo_pages.count_pages(conn, run_id) == 2


def test_sitemap_urls_roundtrip(conn: sqlite3.Connection) -> None:
    run_id = repo_runs.create_run(conn, "https://e.com/", {})
    repo_pages.insert_sitemap_urls(conn, run_id, {"https://e.com/s1", "https://e.com/s2"})
    assert repo_pages.sitemap_urls(conn, run_id) == {"https://e.com/s1", "https://e.com/s2"}


def test_a_long_dead_running_row_is_reconciled(conn: sqlite3.Connection) -> None:
    """A killed process leaves `running` forever, and `previous_run` would diff against it."""
    run_id = repo_runs.create_run(conn, "https://e.com/", {"max_pages": 10})
    conn.execute(
        "UPDATE runs SET started_at = ? WHERE id = ?", ("2020-01-01T00:00:00+00:00", run_id)
    )
    conn.commit()
    assert repo_runs.reconcile_interrupted(conn, older_than_seconds=1800) == 1
    run = repo_runs.get_run(conn, run_id)
    assert run is not None and run.status == "failed"
    assert run.error is not None and "interrupted" in run.error


def test_a_crawl_still_inside_the_wall_clock_is_left_alone(conn: sqlite3.Connection) -> None:
    """A CLI crawl may be running against this database right now; do not kill its row."""
    repo_runs.create_run(conn, "https://e.com/", {"max_pages": 10})
    assert repo_runs.reconcile_interrupted(conn, older_than_seconds=1800) == 0


def test_a_finished_run_is_never_touched(conn: sqlite3.Connection) -> None:
    run_id = repo_runs.create_run(conn, "https://e.com/", {"max_pages": 10})
    repo_runs.finish_run(conn, run_id, "complete", pages=4)
    conn.execute(
        "UPDATE runs SET started_at = ? WHERE id = ?", ("2020-01-01T00:00:00+00:00", run_id)
    )
    conn.commit()
    assert repo_runs.reconcile_interrupted(conn, older_than_seconds=1800) == 0
    run = repo_runs.get_run(conn, run_id)
    assert run is not None and run.status == "complete"


def test_a_row_just_inside_the_window_is_left_alone_and_just_outside_is_reconciled(
    conn: sqlite3.Connection,
) -> None:
    """Boundary check on the string comparison between `started_at` and the cutoff.

    `started_at` is written by `_now()` with second precision; the cutoff carries
    microseconds. A row started 1799s ago is still within the 1800s window and must
    survive; one started 1801s ago is outside it and must be reconciled.
    """
    inside_id = repo_runs.create_run(conn, "https://e.com/inside", {})
    outside_id = repo_runs.create_run(conn, "https://e.com/outside", {})
    inside_started = (datetime.now(UTC) - timedelta(seconds=1799)).isoformat(timespec="seconds")
    outside_started = (datetime.now(UTC) - timedelta(seconds=1801)).isoformat(timespec="seconds")
    conn.execute("UPDATE runs SET started_at = ? WHERE id = ?", (inside_started, inside_id))
    conn.execute("UPDATE runs SET started_at = ? WHERE id = ?", (outside_started, outside_id))
    conn.commit()
    assert repo_runs.reconcile_interrupted(conn, older_than_seconds=1800) == 1
    inside = repo_runs.get_run(conn, inside_id)
    outside = repo_runs.get_run(conn, outside_id)
    assert inside is not None and inside.status == "running"
    assert outside is not None and outside.status == "failed"
