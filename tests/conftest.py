"""Shared fixtures. Tests never read a real .env or touch the network."""

from __future__ import annotations

import sqlite3
import ssl
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest

from seo_scout.store import db

_ENV_KEYS = (
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "SLACK_WEBHOOK_URL",
    "SEO_SCOUT_DB",
    "SEO_SCOUT_MAX_PAGES",
    "SEO_SCOUT_MAX_DEPTH",
    "SEO_SCOUT_MIN_DELAY",
    "SEO_SCOUT_MAX_CONCURRENCY",
    "SEO_SCOUT_MAX_COST_USD",
    "SEO_SCOUT_DASHBOARD_TOKEN",
    "SEO_SCOUT_ALLOWED_DOMAINS",
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Start every test from documented defaults in an empty working directory."""
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)


@pytest.fixture(scope="session")
def ssl_context() -> ssl.SSLContext:
    """Building an SSL context costs ~300 ms on Windows; do it once per session."""
    return ssl.create_default_context()


@pytest.fixture
async def client(ssl_context: ssl.SSLContext) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(verify=ssl_context) as c:
        yield c


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    """A fresh in-memory database with the schema applied, closed after the test."""
    connection = db.connect(":memory:")
    try:
        yield connection
    finally:
        connection.close()
