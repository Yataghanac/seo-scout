"""Pure audit engine: context building and rule evaluation. No I/O here."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

import seo_scout.audit.rules  # noqa: F401 - registers every rule
from seo_scout.audit.context import CrawlContext, text_key
from seo_scout.audit.page import AuditPage
from seo_scout.audit.registry import RULES
from seo_scout.models import Issue


def build_context(
    pages: Iterable[AuditPage],
    *,
    inbound: dict[str, int],
    sitemap_urls: set[str],
    status_by_url: dict[str, int],
) -> CrawlContext:
    titles: Counter[str] = Counter()
    metas: Counter[str] = Counter()
    for page in pages:
        if key := text_key(page.parsed.title):
            titles[key] += 1
        if key := text_key(page.parsed.meta_description):
            metas[key] += 1
    return CrawlContext(
        title_counts=dict(titles),
        meta_counts=dict(metas),
        inbound=inbound,
        sitemap_urls=sitemap_urls,
        status_by_url=status_by_url,
    )


def audit_page(page: AuditPage, ctx: CrawlContext) -> list[Issue]:
    return [
        Issue(rule_id=rule.id, severity=rule.severity, message=message)
        for rule in RULES
        for message in rule.check(page, ctx)
    ]


def audit_pages(pages: Iterable[AuditPage], ctx: CrawlContext) -> dict[str, list[Issue]]:
    return {page.url: audit_page(page, ctx) for page in pages}
