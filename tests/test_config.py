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


def test_dashboard_auth_is_off_by_default() -> None:
    settings = Settings()
    assert settings.dashboard_token is None
    assert settings.allowed_domains == []


def test_allowed_domains_parses_a_comma_separated_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEO_SCOUT_ALLOWED_DOMAINS", "client.com, other.co.uk ")
    assert Settings().allowed_domains == ["client.com", "other.co.uk"]


def test_the_token_is_redacted_in_the_effective_config_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SEO_SCOUT_DASHBOARD_TOKEN", "hunter2")
    line = Settings().effective_config_line()
    assert "hunter2" not in line
    assert "dashboard_token=set" in line
