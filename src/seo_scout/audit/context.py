"""Site-wide facts computed once per run and shared by every rule."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_WS = re.compile(r"\s+")


def text_key(value: str | None) -> str | None:
    """Case- and whitespace-insensitive key for duplicate detection."""
    if value is None:
        return None
    key = _WS.sub(" ", value).strip().casefold()
    return key or None


@dataclass(frozen=True)
class CrawlContext:
    title_counts: dict[str, int] = field(default_factory=dict)
    meta_counts: dict[str, int] = field(default_factory=dict)
    inbound: dict[str, int] = field(default_factory=dict)
    sitemap_urls: set[str] = field(default_factory=set)
    status_by_url: dict[str, int] = field(default_factory=dict)
