from seo_scout.audit.context import CrawlContext
from seo_scout.audit.page import AuditPage
from seo_scout.audit.registry import rule
from seo_scout.models import Severity

_BLOCKING = ("noindex", "nofollow", "none")


def _directives(value: str | None) -> set[str]:
    return {d.strip().lower() for d in (value or "").split(",") if d.strip()}


@rule(
    "noindex",
    Severity.critical,
    "A noindex robots meta removes the page from search results entirely.",
)
def noindex(page: AuditPage, _ctx: CrawlContext) -> list[str]:
    directives = _directives(page.parsed.robots_meta)
    if directives & {"noindex", "none"}:
        return [f'Robots meta says "{page.parsed.robots_meta}"']
    return []


@rule(
    "nofollow",
    Severity.warning,
    "A nofollow robots meta stops crawlers following any link on the page.",
)
def nofollow(page: AuditPage, _ctx: CrawlContext) -> list[str]:
    directives = _directives(page.parsed.robots_meta)
    if directives & {"nofollow", "none"}:
        return [f'Robots meta says "{page.parsed.robots_meta}"']
    return []


@rule(
    "x_robots_tag",
    Severity.critical,
    "An X-Robots-Tag header blocks indexing invisibly; it never shows up in the HTML.",
)
def x_robots_tag(page: AuditPage, _ctx: CrawlContext) -> list[str]:
    value = page.headers.get("x-robots-tag")
    if value and any(b in value.lower() for b in _BLOCKING):
        return [f'X-Robots-Tag header: "{value}"']
    return []
