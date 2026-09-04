from seo_scout.audit.context import CrawlContext
from seo_scout.audit.page import AuditPage
from seo_scout.audit.registry import rule
from seo_scout.models import Severity


@rule(
    "lang_missing",
    Severity.notice,
    "<html lang> tells search engines and screen readers which language the page is in.",
)
def lang_missing(page: AuditPage, _ctx: CrawlContext) -> list[str]:
    return ["<html> has no lang attribute"] if page.parsed.lang is None else []


@rule(
    "og_missing",
    Severity.notice,
    "Open Graph title and description control how the page looks when shared on social media.",
)
def og_missing(page: AuditPage, _ctx: CrawlContext) -> list[str]:
    missing = [
        name
        for name, value in (
            ("og:title", page.parsed.og_title),
            ("og:description", page.parsed.og_description),
        )
        if value is None
    ]
    return [f"Missing Open Graph tags: {', '.join(missing)}"] if missing else []
