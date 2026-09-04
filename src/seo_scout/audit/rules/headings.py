from seo_scout.audit.context import CrawlContext
from seo_scout.audit.page import AuditPage
from seo_scout.audit.registry import rule
from seo_scout.models import Severity


@rule(
    "h1_missing",
    Severity.warning,
    "The <h1> tells crawlers and screen readers what the page is about.",
)
def h1_missing(page: AuditPage, _ctx: CrawlContext) -> list[str]:
    return ["Page has no <h1>"] if not page.parsed.h1s else []


@rule(
    "h1_multiple",
    Severity.notice,
    "Several <h1> elements dilute the page's main topic.",
)
def h1_multiple(page: AuditPage, _ctx: CrawlContext) -> list[str]:
    n = len(page.parsed.h1s)
    return [f"{n} <h1> elements (expected exactly one)"] if n > 1 else []
