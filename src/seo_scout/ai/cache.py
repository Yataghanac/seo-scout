"""Content-hash cache: an unchanged page never costs a second API call."""

from __future__ import annotations

import hashlib
import sqlite3

from pydantic import BaseModel

from seo_scout.ai.schema import Suggestion
from seo_scout.ai.validate import PageFacts
from seo_scout.store import repo_ai

BODY_PREFIX_CHARS = 2000


class CachedSuggestion(BaseModel):
    status: str  # ok | repaired | rejected
    suggestion: Suggestion | None
    reason: str | None


def cache_key(facts: PageFacts, model: str, prompt_version: str) -> str:
    """sha256(url | title | meta | first 2k of body | model | prompt_version)."""
    parts = [
        facts.url,
        facts.title or "",
        facts.meta_description or "",
        facts.body_text[:BODY_PREFIX_CHARS],
        model,
        prompt_version,
    ]
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def get_cached(conn: sqlite3.Connection, key: str) -> CachedSuggestion | None:
    row = repo_ai.get_cache_row(conn, key)
    if row is None:
        return None
    suggestion = (
        Suggestion.model_validate_json(row.suggestion_json) if row.suggestion_json else None
    )
    return CachedSuggestion(status=row.status, suggestion=suggestion, reason=row.reason)


def put_cached(
    conn: sqlite3.Connection, key: str, model: str, prompt_version: str, entry: CachedSuggestion
) -> None:
    """Caller owns the transaction."""
    payload = entry.suggestion.model_dump_json() if entry.suggestion else None
    row = repo_ai.CacheRow(model, prompt_version, entry.status, payload, entry.reason)
    repo_ai.put_cache_row(conn, key, row)
