"""Seed discovery from sitemaps, falling back to the homepage."""

from __future__ import annotations

import logging
from collections import deque
from urllib.parse import urljoin

import httpx
from defusedxml import ElementTree
from pydantic import BaseModel

from seo_scout.urls import normalize, same_site

log = logging.getLogger("seo_scout.crawler.sitemap")

MAX_SITEMAP_FILES = 50


class Seeds(BaseModel):
    urls: list[str]
    from_sitemap: set[str]


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def parse_sitemap(xml: str) -> tuple[list[str], list[str]]:
    """Return (child sitemap URLs, page URLs). Malformed XML yields two empty lists."""
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return [], []
    children: list[str] = []
    pages: list[str] = []
    for parent in root.iter():
        kind = _local(parent.tag)
        if kind not in {"sitemap", "url"}:
            continue
        for loc in parent:
            if _local(loc.tag) == "loc" and loc.text:
                (children if kind == "sitemap" else pages).append(loc.text.strip())
    return children, pages


async def _get_text(client: httpx.AsyncClient, url: str, user_agent: str | None) -> str | None:
    headers = {"user-agent": user_agent} if user_agent else {}
    try:
        response = await client.get(url, headers=headers, follow_redirects=True)
    except httpx.HTTPError as exc:
        log.debug("sitemap fetch failed", extra={"url": url, "error": str(exc)})
        return None
    return response.text if response.status_code == 200 else None


async def discover_seeds(
    client: httpx.AsyncClient,
    site_url: str,
    robots_sitemaps: list[str],
    *,
    user_agent: str | None = None,
) -> Seeds:
    """Homepage first, then every same-site URL found in sitemaps (index files followed)."""
    home = normalize(site_url) or site_url
    queue = deque(robots_sitemaps or [urljoin(home, "/sitemap.xml")])
    visited: set[str] = set()
    found: dict[str, None] = {}
    while queue and len(visited) < MAX_SITEMAP_FILES:
        sitemap_url = queue.popleft()
        if sitemap_url in visited:
            continue
        visited.add(sitemap_url)
        text = await _get_text(client, sitemap_url, user_agent)
        if text is None:
            continue
        children, pages = parse_sitemap(text)
        queue.extend(c for c in children if same_site(home, c))
        for raw in pages:
            url = normalize(raw)
            if url and same_site(home, url):
                found.setdefault(url, None)
    from_sitemap = set(found)
    urls = [home, *(u for u in found if u != home)]
    log.info("seeds discovered", extra={"sitemap_files": len(visited), "seeds": len(urls)})
    return Seeds(urls=urls, from_sitemap=from_sitemap)
