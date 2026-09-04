from seo_scout.audit.context import CrawlContext
from seo_scout.audit.page import AuditPage
from seo_scout.audit.registry import rule
from seo_scout.models import Severity

MAX_BYTES = 2 * 1024 * 1024
MAX_MS = 2000


@rule(
    "page_too_heavy",
    Severity.warning,
    "HTML over 2 MB is slow on mobile and may be truncated by crawlers.",
)
def page_too_heavy(page: AuditPage, _ctx: CrawlContext) -> list[str]:
    if page.bytes <= MAX_BYTES:
        return []
    return [f"Page weighs {page.bytes / 1024 / 1024:.1f} MB (limit 2 MB)"]


@rule(
    "slow_response",
    Severity.warning,
    f"Server responses over {MAX_MS} ms hurt both rankings and crawl rate.",
)
def slow_response(page: AuditPage, _ctx: CrawlContext) -> list[str]:
    if page.elapsed_ms <= MAX_MS:
        return []
    return [f"Fetched in {page.elapsed_ms} ms (limit {MAX_MS} ms)"]
