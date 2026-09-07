"""Repository for the `runs` table."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

from seo_scout.models import Run


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _row_to_run(row: sqlite3.Row) -> Run:
    return Run(
        id=row["id"],
        start_url=row["start_url"],
        status=row["status"],
        started_at=datetime.fromisoformat(row["started_at"]),
        finished_at=datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None,
        pages=row["pages"],
        error=row["error"],
        settings=json.loads(row["settings_json"]),
    )


def create_run(conn: sqlite3.Connection, start_url: str, settings: dict[str, Any]) -> int:
    cur = conn.execute(
        "INSERT INTO runs (start_url, status, started_at, settings_json) VALUES (?, ?, ?, ?)",
        (start_url, "running", _now(), json.dumps(settings, default=str)),
    )
    conn.commit()
    return int(cur.lastrowid or 0)


def finish_run(
    conn: sqlite3.Connection, run_id: int, status: str, *, pages: int, error: str | None = None
) -> None:
    conn.execute(
        "UPDATE runs SET status = ?, finished_at = ?, pages = ?, error = ? WHERE id = ?",
        (status, _now(), pages, error, run_id),
    )
    conn.commit()


def get_run(conn: sqlite3.Connection, run_id: int) -> Run | None:
    row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    return _row_to_run(row) if row else None


def list_runs(conn: sqlite3.Connection) -> list[Run]:
    rows = conn.execute("SELECT * FROM runs ORDER BY id DESC").fetchall()
    return [_row_to_run(r) for r in rows]


def reconcile_interrupted(conn: sqlite3.Connection, *, older_than_seconds: int) -> int:
    """Mark long-dead `running` rows as failed. Returns how many were changed.

    A process killed mid-crawl cannot run its own error handler, so the row would stay
    `running` and `previous_run` would pick it as the baseline for the next diff. Only rows
    older than the wall-clock ceiling are touched: a crawl younger than that may be a CLI run
    in progress against this same database.

    `started_at` is written by `_now()` at second precision. The cutoff below is formatted
    the same way (`timespec="seconds"`) so the two strings compare correctly with a plain
    lexicographic `<`; mixing an unpadded or microsecond-precision cutoff with the
    second-precision stored values would risk incorrect ordering.
    """
    cutoff = (datetime.now(UTC) - timedelta(seconds=older_than_seconds)).isoformat(
        timespec="seconds"
    )
    cur = conn.execute(
        "UPDATE runs SET status = 'failed', finished_at = ?, error = ? "
        "WHERE status = 'running' AND started_at < ?",
        (_now(), "interrupted: the process died before the crawl finished", cutoff),
    )
    conn.commit()
    return int(cur.rowcount)


def previous_run(conn: sqlite3.Connection, run_id: int) -> Run | None:
    """The newest earlier, non-failed run of the same start URL, for diffing."""
    current = get_run(conn, run_id)
    if current is None:
        return None
    row = conn.execute(
        """SELECT * FROM runs WHERE start_url = ? AND id < ? AND status != 'failed'
           ORDER BY id DESC LIMIT 1""",
        (current.start_url, run_id),
    ).fetchone()
    return _row_to_run(row) if row else None
