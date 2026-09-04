import sqlite3

import pytest

from seo_scout.ai.cache import CachedSuggestion, cache_key, get_cached, put_cached
from seo_scout.ai.schema import Suggestion
from seo_scout.ai.validate import PageFacts


def facts(**overrides: object) -> PageFacts:
    base: dict[str, object] = {
        "url": "https://e.com/a",
        "title": "T",
        "meta_description": "M",
        "body_text": "body " * 1000,
    }
    base.update(overrides)
    return PageFacts(**base)  # type: ignore[arg-type]


def test_key_is_stable_and_sha256_shaped() -> None:
    a = cache_key(facts(), "gpt-4o", "v1")
    assert a == cache_key(facts(), "gpt-4o", "v1")
    assert len(a) == 64 and all(c in "0123456789abcdef" for c in a)


@pytest.mark.parametrize(
    "change",
    [
        {"url": "https://e.com/b"},
        {"title": "Other"},
        {"meta_description": None},
        {"body_text": "different " * 400},
    ],
)
def test_key_changes_with_every_input(change: dict[str, object]) -> None:
    assert cache_key(facts(**change), "gpt-4o", "v1") != cache_key(facts(), "gpt-4o", "v1")


def test_key_changes_with_model_and_prompt_version() -> None:
    base = cache_key(facts(), "gpt-4o", "v1")
    assert cache_key(facts(), "gpt-4o-mini", "v1") != base
    assert cache_key(facts(), "gpt-4o", "v2") != base


def test_only_the_first_2k_of_body_text_matters() -> None:
    long_a = facts(body_text="x" * 2000 + "AAAA")
    long_b = facts(body_text="x" * 2000 + "BBBB")
    assert cache_key(long_a, "gpt-4o", "v1") == cache_key(long_b, "gpt-4o", "v1")


def test_cache_roundtrip(conn: sqlite3.Connection) -> None:
    key = cache_key(facts(), "gpt-4o", "v1")
    assert get_cached(conn, key) is None
    suggestion = Suggestion(diagnosis="d", title="t", meta_description="m")
    with conn:
        put_cached(
            conn,
            key,
            "gpt-4o",
            "v1",
            CachedSuggestion(status="ok", suggestion=suggestion, reason=None),
        )
    hit = get_cached(conn, key)
    assert hit is not None
    assert hit.status == "ok"
    assert hit.suggestion == suggestion


def test_rejections_are_cached_too(conn: sqlite3.Connection) -> None:
    key = cache_key(facts(), "gpt-4o", "v1")
    with conn:
        rejected = CachedSuggestion(status="rejected", suggestion=None, reason="title_too_short")
        put_cached(conn, key, "gpt-4o", "v1", rejected)
    hit = get_cached(conn, key)
    assert hit is not None
    assert hit.status == "rejected"
    assert hit.suggestion is None
    assert hit.reason == "title_too_short"
