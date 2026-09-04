from seo_scout.audit.context import CrawlContext
from seo_scout.audit.page import AuditPage
from seo_scout.audit.registry import rule
from seo_scout.models import Severity


@rule(
    "redirect_chain",
    Severity.warning,
    "Each extra redirect hop adds latency and leaks link equity; one hop is normal, more is not.",
)
def redirect_chain(page: AuditPage, _ctx: CrawlContext) -> list[str]:
    hops = page.redirect_chain
    if len(hops) <= 1:
        return []
    path = " -> ".join(f"{h.url} ({h.status})" for h in hops) + f" -> {page.final_url}"
    return [f"Reached through {len(hops)} redirects: {path}"]
