"""The README is the front door, and its numbers are claims like any other.

Prose drifts silently: nothing fails when a rule is added and the diagram still says the old
count. These pin the few numbers that describe code, in the same spirit as the test that keeps
`.env.example` and the packaged template identical.
"""

from __future__ import annotations

import re
from pathlib import Path

import seo_scout.audit.rules  # noqa: F401 - importing registers every rule
from seo_scout.audit.registry import RULES
from seo_scout.config import Settings

README = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")


def _one(pattern: str) -> str:
    found = re.findall(pattern, README)
    assert len(found) == 1, f"expected exactly one {pattern!r} in README.md, found {found}"
    return found[0]


def test_the_architecture_diagram_states_the_real_rule_count() -> None:
    assert int(_one(r"(\d+) pure rules")) == len(RULES)


def test_the_fixture_paragraph_states_the_real_rule_count() -> None:
    assert int(_one(r"of the (\d+) rules")) == len(RULES)


def test_the_documented_caps_are_the_configured_caps() -> None:
    """The Crawling policy bullet promises specific ceilings; config.py has to agree."""
    settings = Settings()
    caps = _one(r"\*\*Hard caps\*\*: ([^.]+)\.")
    assert f"{settings.max_pages} pages" in caps
    assert f"depth {settings.max_depth}" in caps
    assert f"{settings.max_response_bytes // 1024 // 1024} MB per response" in caps
    assert f"{settings.wall_clock_seconds // 60} minutes" in caps


def test_the_documented_rate_limit_is_the_configured_one() -> None:
    settings = Settings()
    assert f"at most {settings.max_concurrency} concurrent requests" in README
    assert f"minimum {settings.min_delay} s between" in README


def test_the_documented_user_agent_is_the_one_sent() -> None:
    assert Settings().user_agent in README
