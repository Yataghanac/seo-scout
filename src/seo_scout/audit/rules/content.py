from seo_scout.audit.context import CrawlContext
from seo_scout.audit.page import AuditPage
from seo_scout.audit.registry import rule
from seo_scout.models import Severity

MIN_WORDS = 300


@rule(
    "thin_content",
    Severity.warning,
    f"Pages with under {MIN_WORDS} words of visible text rarely rank; they look empty to crawlers.",
)
def thin_content(page: AuditPage, _ctx: CrawlContext) -> list[str]:
    words = page.parsed.word_count
    if words >= MIN_WORDS:
        return []
    return [f"Only {words} words of visible text (minimum {MIN_WORDS})"]
