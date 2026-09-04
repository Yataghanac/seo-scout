"""Shared fixtures. Tests never read a real .env or touch the network."""

from __future__ import annotations

from pathlib import Path

import pytest

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
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Start every test from documented defaults in an empty working directory."""
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
