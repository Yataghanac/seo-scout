"""Seed discovery from sitemaps, falling back to the homepage."""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

import httpx
from defusedxml import ElementTree
from pydantic import BaseModel

from seo_scout.crawler.fetch import UNFETCHABLE
from seo_scout.urls import Link, link_pair, resolve, same_site

log = logging.getLogger("seo_scout.crawler.sitemap")

# Per source: the start path's own sitemaps and the host's declared ones each get this many
# files, so a large index on one side cannot starve the other.
MAX_SITEMAP_FILES = 50
MAX_PREFIX_GUESSES = 3
# A same-site sitemap.xml commonly 302s http -> https; robots.txt can also name a sitemap
# that redirects once. Three hops covers real sites without giving a hostile Location chain
# room to stall discovery.
MAX_SITEMAP_REDIRECTS = 3
_SITEMAP_ROOTS = {"urlset", "sitemapindex"}
_REDIRECTS = {301, 302, 303, 307, 308}


class Seeds(BaseModel):
    """Start URLs as `Link(key, url)`: the start as typed, then what the sitemaps listed."""

    pairs: list[Link]
    from_sitemap: set[str]  # identity keys the sitemaps named (the start only if listed)

    @property
    def urls(self) -> list[str]:
        """Request spellings, in crawl order."""
        return [pair.url for pair in self.pairs]


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def parse_sitemap(xml: str) -> tuple[list[str], list[str]] | None:
    """Return (child sitemap URLs, page URLs), or None when the text is not a sitemap.

    Malformed XML and well-formed non-sitemap XML (an HTML shell a host serves with 200
    for every path) both give None, so a soft 404 is not mistaken for an empty sitemap.
    """
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return None
    if _local(root.tag) not in _SITEMAP_ROOTS:
        return None
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


def _redirect_target(current: str, location: str) -> str | None:
    """Wire form of a `Location` header, or None when no crawler could follow it."""
    try:
        return resolve(urljoin(current, location))
    except ValueError:
        return None


async def _get_text(
    client: httpx.AsyncClient,
    url: str,
    user_agent: str | None,
    allowed: Callable[[str], bool],
) -> str | None:
    """The file's text on a 200, else None: unreachable, unrequestable, refused and 404 read
    alike.

    Redirects are followed by hand, `allowed` re-checked before every request including the
    first: `follow_redirects=True` would hand a `Location` straight to the network with no
    check at all, and `Fetcher.fetch` cannot be reused here because it also enforces an HTML
    content-type, which would reject sitemap XML as `non_html`.
    """
    headers = {"user-agent": user_agent} if user_agent else {}
    current = url
    for _ in range(MAX_SITEMAP_REDIRECTS + 1):
        if not allowed(current):
            log.debug("sitemap fetch refused by target policy", extra={"url": current})
            return None
        try:
            response = await client.get(current, headers=headers, follow_redirects=False)
        except UNFETCHABLE as exc:
            log.debug("sitemap fetch failed", extra={"url": current, "error": str(exc)})
            return None
        if response.status_code == 200:
            return response.text
        location = response.headers.get("location")
        if response.status_code not in _REDIRECTS or not location:
            return None
        target = _redirect_target(current, location)
        if target is None:
            return None
        current = target
    return None


def _root_guess(home: str) -> str:
    return urljoin(home, "/sitemap.xml")


def _prefix_guesses(start: str) -> list[str]:
    """Sitemap candidates under the start path, deepest segment first, at most three.

    Docs generators write the sitemap at the site's base path and one host often serves
    several such sites, so `/uv/guides/` is worth trying `/uv/guides/sitemap.xml` and then
    `/uv/sitemap.xml`. No guessing whether the last segment is a file: `/3.12` and
    `/index.html` look alike, so `/docs/index.html/sitemap.xml` costs one 404 and the walk
    moves on to `/docs/sitemap.xml`. Query strings are not part of the path.
    """
    segments = [s for s in urlsplit(start).path.split("/") if s]
    guesses: list[str] = []
    while segments and len(guesses) < MAX_PREFIX_GUESSES:
        guesses.append(urljoin(start, "/" + "/".join(segments) + "/sitemap.xml"))
        segments.pop()
    return guesses


@dataclass
class _Discovery:
    client: httpx.AsyncClient
    home: str
    user_agent: str | None
    allowed: Callable[[str], bool]
    visited: set[str] = field(default_factory=set)
    found: dict[str, Link] = field(default_factory=dict)  # identity key -> first Link seen
    parsed_files: int = 0  # files that were sitemaps, as opposed to locations tried

    async def drain(self, start: list[str], budget: int = MAX_SITEMAP_FILES) -> bool:
        """Fetch these sitemaps and their index children, at most `budget` files.

        True if at least one parsed as a sitemap. `visited` is shared across calls so no
        file is fetched twice; the budget is per call so one source cannot starve another.
        Every URL a sitemap names is untrusted text: it goes through `link_pair` first, and
        `allowed` (robots.txt composed with `target_ok`) is checked on every fetch here too —
        a `Sitemap:` line or a redirect can point off the site being crawled entirely.
        """
        queue, hit, fetched = deque(start), False, 0
        while queue and fetched < budget:
            sitemap_url = queue.popleft()
            if sitemap_url in self.visited:
                continue
            self.visited.add(sitemap_url)
            fetched += 1
            text = await _get_text(self.client, sitemap_url, self.user_agent, self.allowed)
            if text is None or (parsed := parse_sitemap(text)) is None:
                continue
            hit = True
            self.parsed_files += 1
            children, pages = parsed
            queue.extend(c.url for c in map(link_pair, children) if c and self._mine(c))
            for link in map(link_pair, pages):
                if link and self._mine(link):
                    self.found.setdefault(link.key, link)
        return hit

    def _mine(self, link: Link) -> bool:
        return same_site(self.home, link.key)


async def discover_seeds(
    client: httpx.AsyncClient,
    site_url: str,
    robots_sitemaps: list[str],
    *,
    user_agent: str | None = None,
    allowed: Callable[[str], bool] | None = None,
) -> Seeds:
    """Start URL first, then every same-site URL found in sitemaps (index files followed).

    The start path's own sitemap comes first: its segments are tried deepest-first until
    one answers, so the site the user asked for is read before anything else. Then the
    sitemaps robots.txt names are drained (a guess already read is not fetched twice).
    Only when robots names none and no guess answered is the host root `/sitemap.xml`
    tried: after a hit it would only seed the host's sibling sites. URLs are kept as the
    sitemap spelled them (that is what gets requested) with their identity keys alongside.

    `allowed` gates every sitemap fetch and every redirect hop it follows (see `_get_text`);
    it defaults to permit-all so the CLI, which has no second gate beside robots.txt, is
    unaffected.
    """
    home, start = link_pair(site_url) or Link(site_url, site_url)
    discovery = _Discovery(client, home, user_agent, allowed or (lambda _url: True))
    hit = False
    for guess in _prefix_guesses(start):
        if hit := await discovery.drain([guess]):
            break
    if robots_sitemaps:
        await discovery.drain(robots_sitemaps)
    elif not hit:
        await discovery.drain([_root_guess(home)])
    found = discovery.found
    pairs = [Link(home, start), *(link for key, link in found.items() if key != home)]
    # Two numbers, because a site with no sitemap still costs a handful of 404s: the
    # files that parsed answer "what did we read", the fetches answer "where did we look".
    log.info(
        "seeds discovered",
        extra={
            "sitemap_files": discovery.parsed_files,
            "sitemap_fetches": len(discovery.visited),
            "seeds": len(pairs),
        },
    )
    return Seeds(pairs=pairs, from_sitemap=set(found))
