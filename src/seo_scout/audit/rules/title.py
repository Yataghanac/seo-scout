from seo_scout.audit.context import CrawlContext, text_key
from seo_scout.audit.page import AuditPage
from seo_scout.audit.registry import rule
from seo_scout.models import Severity

TITLE_MIN = 30
TITLE_MAX = 60


@rule(
    "title_missing",
    Severity.critical,
    "Search engines use the <title> as the headline of the result. Without one they invent it.",
)
def title_missing(page: AuditPage, _ctx: CrawlContext) -> list[str]:
    return ["Page has no <title>"] if page.parsed.title is None else []


@rule(
    "title_too_short",
    Severity.warning,
    f"Titles under {TITLE_MIN} characters waste the space searchers use to decide to click.",
)
def title_too_short(page: AuditPage, _ctx: CrawlContext) -> list[str]:
    title = page.parsed.title
    if title is None or len(title) >= TITLE_MIN:
        return []
    return [f'Title is {len(title)} characters (minimum {TITLE_MIN}): "{title}"']


@rule(
    "title_too_long",
    Severity.warning,
    f"Titles over {TITLE_MAX} characters are truncated in results, hiding the end.",
)
def title_too_long(page: AuditPage, _ctx: CrawlContext) -> list[str]:
    title = page.parsed.title
    if title is None or len(title) <= TITLE_MAX:
        return []
    return [f'Title is {len(title)} characters (maximum {TITLE_MAX}): "{title}"']


@rule(
    "title_duplicate",
    Severity.warning,
    "Pages sharing a title compete with each other and look like duplicates to crawlers.",
)
def title_duplicate(page: AuditPage, ctx: CrawlContext) -> list[str]:
    key = text_key(page.parsed.title)
    count = ctx.title_counts.get(key, 0) if key else 0
    if count < 2:
        return []
    return [f'Title is used on {count} pages: "{page.parsed.title}"']
