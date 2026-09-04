"""Single source of configuration: env-driven pydantic-settings object."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from seo_scout import __version__

GITHUB_URL = "https://github.com/Yataghanac/seo-scout"


class Settings(BaseSettings):
    """Effective runtime configuration. Every value has a documented default."""

    model_config = SettingsConfigDict(
        env_prefix="SEO_SCOUT_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Crawl caps. These are ceilings the CLI can lower but never raise past hard limits.
    max_pages: int = Field(default=500, ge=1, le=5000)
    max_depth: int = Field(default=5, ge=0, le=10)
    max_response_bytes: int = Field(default=5 * 1024 * 1024, ge=1024)
    wall_clock_seconds: int = Field(default=1800, ge=10)
    min_delay: float = Field(default=0.5, ge=0.0)
    max_concurrency: int = Field(default=2, ge=1, le=8)
    request_timeout: float = Field(default=15.0, gt=0)

    # Storage
    db: str = "seo_scout.db"

    # AI layer
    openai_api_key: str | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o-2024-08-06", validation_alias="OPENAI_MODEL")
    max_cost_usd: float = Field(default=1.0, ge=0.0)
    ai_concurrency: int = Field(default=5, ge=1, le=20)

    # Automation
    slack_webhook_url: str | None = Field(default=None, validation_alias="SLACK_WEBHOOK_URL")

    @property
    def user_agent(self) -> str:
        major_minor = ".".join(__version__.split(".")[:2])
        return f"SEOScout/{major_minor} (+{GITHUB_URL})"

    def effective_config_line(self) -> str:
        """One printable line of the effective config with secrets redacted."""
        secrets = {"openai_api_key", "slack_webhook_url"}
        parts = [
            f"{name}={('set' if value else 'unset') if name in secrets else value}"
            for name, value in self.model_dump().items()
        ]
        return "config " + " ".join(parts)
