"""Repository for `pages`, `links`, and `sitemap_urls`."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import datetime

from seo_scout.models import FetchedPage, RedirectHop


def insert_page(conn: sqlite3.Connection, run_id: int, page: FetchedPage) -> None:
    """Insert one page; a second insert for the same (run, url) is ignored."""
    conn.execute(
        """INSERT OR IGNORE INTO pages
           (run_id, url, final_url, status, depth, content_type, bytes, elapsed_ms,
            fetched_at, html, redirect_chain_json, headers_json, error)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            run_id,
            page.url,
            page.final_url,
            page.status,
            page.depth,
            page.content_type,
            page.bytes,
            page.elapsed_ms,
            page.fetched_at.isoformat(),
            page.html,
            json.dumps([h.model_dump() for h in page.redirect_chain]),
            json.dumps(page.headers),
            page.error,
        ),
    )
    conn.commit()


def _row_to_page(row: sqlite3.Row) -> FetchedPage:
    return FetchedPage(
        url=row["url"],
        final_url=row["final_url"],
        status=row["status"],
        depth=row["depth"],
        content_type=row["content_type"],
        bytes=row["bytes"],
        elapsed_ms=row["elapsed_ms"],
        fetched_at=datetime.fromisoformat(row["fetched_at"]),
        html=row["html"],
        redirect_chain=[RedirectHop(**h) for h in json.loads(row["redirect_chain_json"])],
        headers=json.loads(row["headers_json"]),
        error=row["error"],
    )


def list_pages(conn: sqlite3.Connection, run_id: int) -> list[FetchedPage]:
    rows = conn.execute("SELECT * FROM pages WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()
    return [_row_to_page(r) for r in rows]


def count_pages(conn: sqlite3.Connection, run_id: int) -> int:
    row = conn.execute("SELECT COUNT(*) FROM pages WHERE run_id = ?", (run_id,)).fetchone()
    return int(row[0])


def status_by_url(conn: sqlite3.Connection, run_id: int) -> dict[str, int]:
    rows = conn.execute("SELECT url, status FROM pages WHERE run_id = ?", (run_id,)).fetchall()
    return {r["url"]: r["status"] for r in rows}


def insert_links(
    conn: sqlite3.Connection, run_id: int, from_url: str, to_urls: Iterable[str]
) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO links (run_id, from_url, to_url) VALUES (?, ?, ?)",
        [(run_id, from_url, to_url) for to_url in to_urls],
    )
    conn.commit()


def inbound_counts(conn: sqlite3.Connection, run_id: int) -> dict[str, int]:
    """Distinct internal referrers per target URL."""
    rows = conn.execute(
        "SELECT to_url, COUNT(*) AS n FROM links WHERE run_id = ? GROUP BY to_url", (run_id,)
    ).fetchall()
    return {r["to_url"]: r["n"] for r in rows}


def insert_sitemap_urls(conn: sqlite3.Connection, run_id: int, urls: Iterable[str]) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO sitemap_urls (run_id, url) VALUES (?, ?)",
        [(run_id, url) for url in urls],
    )
    conn.commit()


def sitemap_urls(conn: sqlite3.Connection, run_id: int) -> set[str]:
    rows = conn.execute("SELECT url FROM sitemap_urls WHERE run_id = ?", (run_id,)).fetchall()
    return {r["url"] for r in rows}
