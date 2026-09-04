from seo_scout.audit.context import CrawlContext
from seo_scout.audit.page import AuditPage
from seo_scout.audit.registry import rule
from seo_scout.models import Severity
from seo_scout.urls import normalize, same_site


@rule(
    "canonical_missing",
    Severity.notice,
    "A self-referencing canonical protects the page from being treated as a duplicate of "
    "parameterised or mirrored copies.",
)
def canonical_missing(page: AuditPage, _ctx: CrawlContext) -> list[str]:
    return ["Page has no canonical link"] if page.parsed.canonical is None else []


@rule(
    "canonical_conflict",
    Severity.warning,
    "A canonical pointing at another page tells search engines to index that page instead.",
)
def canonical_conflict(page: AuditPage, _ctx: CrawlContext) -> list[str]:
    canonical = page.parsed.canonical
    if canonical is None or not same_site(page.url, canonical):
        return []
    if normalize(canonical) == normalize(page.final_url):
        return []
    return [f"Canonical points to {canonical}, not this page"]


@rule(
    "canonical_off_domain",
    Severity.critical,
    "A canonical on another domain hands this page's ranking to that domain.",
)
def canonical_off_domain(page: AuditPage, _ctx: CrawlContext) -> list[str]:
    canonical = page.parsed.canonical
    if canonical is None or same_site(page.url, canonical):
        return []
    return [f"Canonical points off-site to {canonical}"]
