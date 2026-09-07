"""BackgroundCrawler: one crawl at a time, never wedged by a failure. No test touches the
network or a real database — `_crawl` is always injected."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from seo_scout.config import Settings
from seo_scout.crawl_runner import BackgroundCrawler


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(db=str(tmp_path / "t.db"))


async def test_start_locks_out_a_second_crawl_until_the_first_finishes(
    settings: Settings,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_crawl(url: str, max_pages: int | None) -> None:
        started.set()
        await release.wait()

    runner = BackgroundCrawler(settings, _crawl=fake_crawl)
    assert runner.start("https://e.com/", None) is True
    await started.wait()
    assert runner.start("https://e.com/", None) is False  # still running

    release.set()
    await runner.wait()
    assert runner.start("https://e.com/", None) is True  # free again


async def test_a_failed_crawl_does_not_wedge_the_runner(settings: Settings) -> None:
    async def boom(url: str, max_pages: int | None) -> None:
        raise RuntimeError("boom")

    runner = BackgroundCrawler(settings, _crawl=boom)
    assert runner.start("https://e.com/", None) is True
    await runner.wait()
    assert runner.start("https://e.com/", None) is True
