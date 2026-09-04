from seo_scout.audit.context import CrawlContext
from seo_scout.audit.page import AuditPage
from seo_scout.audit.registry import rule
from seo_scout.models import Severity


@rule(
    "images_missing_alt",
    Severity.notice,
    "Images without an alt attribute are invisible to image search and screen readers. "
    'An empty alt="" is fine for decorative images and is not flagged.',
)
def images_missing_alt(page: AuditPage, _ctx: CrawlContext) -> list[str]:
    missing = page.parsed.images_missing_alt
    if missing == 0:
        return []
    return [f"{missing} of {page.parsed.images_total} images have no alt attribute"]
