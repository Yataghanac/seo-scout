"""The unit every rule inspects: fetch metadata plus parsed HTML facts."""

from __future__ import annotations

from functools import cached_property
from typing import TypedDict, Unpack

from pydantic import BaseModel, ConfigDict

from seo_scout.models import FetchedPage, RedirectHop
from seo_scout.parse import ParsedPage, parse_html
from seo_scout.urls import same_site


class FetchMeta(TypedDict, total=False):
    """Optional fetch facts for `AuditPage.build`; defaults describe a plain 200 response."""

    status: int
    bytes: int
    elapsed_ms: int
    headers: dict[str, str] | None
    redirect_chain: list[RedirectHop] | list[dict[str, object]] | None


class AuditPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    url: str
    final_url: str
    status: int
    bytes: int
    elapsed_ms: int
    headers: dict[str, str]
    redirect_chain: list[RedirectHop]
    parsed: ParsedPage

    @classmethod
    def build(cls, *, url: str, final_url: str, html: str, **meta: Unpack[FetchMeta]) -> AuditPage:
        headers = meta.get("headers") or {}
        return cls(
            url=url,
            final_url=final_url,
            status=meta.get("status", 200),
            bytes=meta.get("bytes", 0),
            elapsed_ms=meta.get("elapsed_ms", 0),
            headers={k.lower(): v for k, v in headers.items()},
            redirect_chain=meta.get("redirect_chain") or [],
            parsed=parse_html(html, final_url),
        )

    @classmethod
    def from_fetched(cls, page: FetchedPage) -> AuditPage | None:
        """Only pages with an HTML body are auditable."""
        if page.html is None:
            return None
        return cls.build(
            url=page.url,
            final_url=page.final_url,
            html=page.html,
            status=page.status,
            bytes=page.bytes,
            elapsed_ms=page.elapsed_ms,
            headers=page.headers,
            redirect_chain=page.redirect_chain,
        )

    @cached_property
    def internal_links(self) -> list[str]:
        """Identity keys of same-site link targets: what `status_by_url` is keyed by."""
        return [link.key for link in self.parsed.links if same_site(self.url, link.key)]
