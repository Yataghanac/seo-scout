import pytest

from seo_scout.config import Settings


def test_defaults_match_documented_values() -> None:
    s = Settings()
    assert s.max_pages == 500
    assert s.max_depth == 5
    assert s.min_delay == 0.5
    assert s.max_concurrency == 2
    assert s.max_response_bytes == 5 * 1024 * 1024
    assert s.openai_api_key is None
    assert s.openai_model == "gpt-4o-2024-08-06"


def test_env_overrides_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEO_SCOUT_MAX_PAGES", "7")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    s = Settings()
    assert s.max_pages == 7
    assert s.openai_api_key == "sk-test"


def test_effective_config_line_redacts_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-value")
    line = Settings().effective_config_line()
    assert "sk-secret-value" not in line
    assert "openai_api_key=set" in line
    assert "max_pages=500" in line


def test_user_agent_identifies_honestly() -> None:
    assert Settings().user_agent == "SEOScout/0.1 (+https://github.com/Yataghanac/seo-scout)"
