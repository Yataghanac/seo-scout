from seo_scout.audit.context import CrawlContext, text_key
from seo_scout.audit.page import AuditPage
from seo_scout.audit.registry import rule
from seo_scout.models import Severity

META_MIN = 70
META_MAX = 160


@rule(
    "meta_description_missing",
    Severity.warning,
    "Without a meta description, search engines pick an arbitrary snippet from the page.",
)
def meta_missing(page: AuditPage, _ctx: CrawlContext) -> list[str]:
    return ["Page has no meta description"] if page.parsed.meta_description is None else []


@rule(
    "meta_description_too_short",
    Severity.notice,
    f"Descriptions under {META_MIN} characters rarely say enough to earn the click.",
)
def meta_too_short(page: AuditPage, _ctx: CrawlContext) -> list[str]:
    meta = page.parsed.meta_description
    if meta is None or len(meta) >= META_MIN:
        return []
    return [f'Meta description is {len(meta)} characters (minimum {META_MIN}): "{meta}"']


@rule(
    "meta_description_too_long",
    Severity.notice,
    f"Descriptions over {META_MAX} characters are cut off in results.",
)
def meta_too_long(page: AuditPage, _ctx: CrawlContext) -> list[str]:
    meta = page.parsed.meta_description
    if meta is None or len(meta) <= META_MAX:
        return []
    return [f'Meta description is {len(meta)} characters (maximum {META_MAX}): "{meta[:80]}..."']


@rule(
    "meta_description_duplicate",
    Severity.warning,
    "A description reused across pages cannot describe any of them well.",
)
def meta_duplicate(page: AuditPage, ctx: CrawlContext) -> list[str]:
    key = text_key(page.parsed.meta_description)
    count = ctx.meta_counts.get(key, 0) if key else 0
    if count < 2:
        return []
    return [f"Meta description is used on {count} pages"]
