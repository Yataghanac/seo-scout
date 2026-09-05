"""Seed discovery from sitemaps, falling back to the homepage."""

from __future__ import annotations

import logging
from collections import deque
from urllib.parse import urlsplit, urlunsplit

import httpx
from defusedxml import ElementTree
from pydantic import BaseModel

from seo_scout.urls import normalize, resolve, same_site

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


def _sitemap_guesses(home: str) -> tuple[str, str | None]:
    """(host-root sitemap, sitemap under the start path or None when the start is the root).

    Docs generators write the sitemap at the site's base path, and one host often serves
    several such sites, so the prefix guess is worth one request whenever the start URL
    names a prefix. Query strings and dotted segments (`/3.12/`) are not part of the path.
    """
    parts = urlsplit(home)
    root = urlunsplit((parts.scheme, parts.netloc, "/sitemap.xml", "", ""))
    prefix = parts.path.strip("/")
    if not prefix:
        return root, None
    return root, urlunsplit((parts.scheme, parts.netloc, f"/{prefix}/sitemap.xml", "", ""))


async def discover_seeds(
    client: httpx.AsyncClient,
    site_url: str,
    robots_sitemaps: list[str],
    *,
    user_agent: str | None = None,
) -> Seeds:
    """Homepage first, then every same-site URL found in sitemaps (index files followed).

    URLs are kept as the sitemap spelled them (that is what gets requested);
    `from_sitemap` holds their normalized keys for the audit.
    """
    home = normalize(site_url) or site_url
    root_guess, prefix_guess = _sitemap_guesses(home)
    queue = deque(robots_sitemaps or [root_guess])
    if prefix_guess:
        queue.append(prefix_guess)
    visited: set[str] = set()
    found: dict[str, str] = {}
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
            key, url = normalize(raw), resolve(raw)
            if key and url and same_site(home, key):
                found.setdefault(key, url)
    from_sitemap = set(found)
    start = resolve(site_url) or site_url
    urls = [start, *(u for k, u in found.items() if k != home)]
    log.info("seeds discovered", extra={"sitemap_files": len(visited), "seeds": len(urls)})
    return Seeds(urls=urls, from_sitemap=from_sitemap)
