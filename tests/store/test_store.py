import sqlite3
from datetime import UTC, datetime

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
