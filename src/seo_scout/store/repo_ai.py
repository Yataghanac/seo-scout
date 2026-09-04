"""Repository for `ai_cache`, `ai_calls`, and `ai_suggestions`. Writers do not commit."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import NamedTuple

from seo_scout.models import SuggestionRow


class CacheRow(NamedTuple):
    model: str
    prompt_version: str
    status: str
    suggestion_json: str | None
    reason: str | None


class CallRecord(NamedTuple):
    url: str
    kind: str  # initial | repair
    prompt_tokens: int
    completion_tokens: int
    usd: float


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def get_cache_row(conn: sqlite3.Connection, key: str) -> CacheRow | None:
    row = conn.execute(
        "SELECT model, prompt_version, status, suggestion_json, reason FROM ai_cache WHERE key = ?",
        (key,),
    ).fetchone()
    return CacheRow(*row) if row else None


def put_cache_row(conn: sqlite3.Connection, key: str, row: CacheRow) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO ai_cache
           (key, model, prompt_version, status, suggestion_json, reason, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (key, *row, _now()),
    )


def record_call(conn: sqlite3.Connection, run_id: int, call: CallRecord) -> None:
    conn.execute(
        """INSERT INTO ai_calls
           (run_id, url, kind, prompt_tokens, completion_tokens, usd, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (run_id, *call, _now()),
    )


def run_cost(conn: sqlite3.Connection, run_id: int) -> tuple[int, int, int, float]:
    """(calls, prompt_tokens, completion_tokens, usd) for one run."""
    row = conn.execute(
        """SELECT COUNT(*) AS calls, COALESCE(SUM(prompt_tokens), 0) AS p,
                  COALESCE(SUM(completion_tokens), 0) AS c, COALESCE(SUM(usd), 0.0) AS usd
           FROM ai_calls WHERE run_id = ?""",
        (run_id,),
    ).fetchone()
    return int(row["calls"]), int(row["p"]), int(row["c"]), float(row["usd"])


def upsert_suggestion(conn: sqlite3.Connection, run_id: int, row: SuggestionRow) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO ai_suggestions
           (run_id, url, original_title, original_meta, diagnosis, title, meta_description,
            status, reason, cached, cost_usd)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            run_id,
            row.url,
            row.original_title,
            row.original_meta,
            row.diagnosis,
            row.title,
            row.meta_description,
            row.status,
            row.reason,
            int(row.cached),
            row.cost_usd,
        ),
    )


def list_suggestions(conn: sqlite3.Connection, run_id: int) -> list[SuggestionRow]:
    rows = conn.execute(
        "SELECT * FROM ai_suggestions WHERE run_id = ? ORDER BY url", (run_id,)
    ).fetchall()
    return [
        SuggestionRow(
            url=r["url"],
            original_title=r["original_title"],
            original_meta=r["original_meta"],
            diagnosis=r["diagnosis"],
            title=r["title"],
            meta_description=r["meta_description"],
            status=r["status"],
            reason=r["reason"],
            cached=bool(r["cached"]),
            cost_usd=r["cost_usd"],
        )
        for r in rows
    ]
