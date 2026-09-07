from urllib.parse import urlsplit

from seo_scout.audit.context import CrawlContext
from seo_scout.audit.page import AuditPage
from seo_scout.audit.registry import rule
from seo_scout.models import Severity

EXAMPLES = 3


@rule(
    "broken_links",
    Severity.critical,
    "Internal links to 4xx/5xx pages waste crawl budget and send visitors to error pages.",
)
def broken_links(page: AuditPage, ctx: CrawlContext) -> list[str]:
    broken = [link for link in page.internal_links if ctx.status_by_url.get(link, 0) >= 400]
    if not broken:
        return []
    shown = ", ".join(broken[:EXAMPLES]) + (" ..." if len(broken) > EXAMPLES else "")
    noun = "link" if len(broken) == 1 else "links"
    return [f"{len(broken)} broken internal {noun}: {shown}"]


@rule(
    "orphan_page",
    Severity.warning,
    "A page in the sitemap that no other page links to gets little crawl attention and no "
    "internal ranking signal.",
)
def orphan_page(page: AuditPage, ctx: CrawlContext) -> list[str]:
    if urlsplit(page.url).path in ("", "/"):
        return []
    if page.url in ctx.sitemap_urls and ctx.inbound.get(page.url, 0) == 0:
        return ["Listed in the sitemap but no internal page links to it"]
    return []
