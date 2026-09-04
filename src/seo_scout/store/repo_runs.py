"""Repository for the `runs` table."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
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
