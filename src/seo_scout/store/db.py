"""SQLite connection and schema management. Knows nothing about other layers."""

from __future__ import annotations

import sqlite3
from importlib import resources


def connect(path: str) -> sqlite3.Connection:
    """Open (or create) the database with WAL, foreign keys, and the schema applied."""
    # check_same_thread=False: the API opens one connection per request and FastAPI may
    # run the dependency and the endpoint on different worker threads.
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if path != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    init_schema(conn)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Idempotent: every statement is CREATE ... IF NOT EXISTS."""
    sql = resources.files("seo_scout.store").joinpath("schema.sql").read_text(encoding="utf-8")
    conn.executescript(sql)
    conn.commit()
