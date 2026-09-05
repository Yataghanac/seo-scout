"""HTML -> facts. Shared by the crawler (links) and audit rules (everything else). Pure."""

from __future__ import annotations

import re

from pydantic import BaseModel
from selectolax.parser import HTMLParser, Node

from seo_scout.urls import normalize, resolve

_WS = re.compile(r"\s+")
_NON_CONTENT_TAGS = ["script", "style", "noscript", "template", "svg"]


class ParsedPage(BaseModel):
    """Everything the audit and AI layers need from one HTML document."""

    title: str | None = None
    meta_description: str | None = None
    canonical: str | None = None
    robots_meta: str | None = None
    og_title: str | None = None
    og_description: str | None = None
    lang: str | None = None
    h1s: list[str] = []
    text: str = ""
    word_count: int = 0
    images_total: int = 0
    images_missing_alt: int = 0
    links: list[str] = []


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    collapsed = _WS.sub(" ", value).strip()
    return collapsed or None


def _node_text(node: Node | None) -> str | None:
    return _clean(node.text()) if node is not None else None


def _meta_map(tree: HTMLParser) -> dict[str, str]:
    """Lower-cased `name`/`property` -> content for every <meta>."""
    found: dict[str, str] = {}
    for node in tree.css("meta"):
        attrs = node.attributes
        key = attrs.get("name") or attrs.get("property")
        content = attrs.get("content")
        if key and content is not None and key.lower() not in found:
            found[key.lower()] = content
    return found


def _links(tree: HTMLParser, base_url: str) -> list[str]:
    """Outgoing links as written (resolved, fragment dropped), one per distinct page."""
    seen: dict[str, str] = {}
    for node in tree.css("a[href]"):
        href = node.attributes.get("href")
        if href and (key := normalize(href, base_url)) and (url := resolve(href, base_url)):
            seen.setdefault(key, url)
    return list(seen.values())


def _canonical(tree: HTMLParser, base_url: str) -> str | None:
    for node in tree.css("link[rel]"):
        rel = (node.attributes.get("rel") or "").lower().split()
        href = node.attributes.get("href")
        if "canonical" in rel and href:
            return normalize(href, base_url) or _clean(href)
    return None


def _lang(tree: HTMLParser) -> str | None:
    html = tree.css_first("html")
    return _clean(html.attributes.get("lang")) if html is not None else None


def _visible_text(tree: HTMLParser) -> str:
    tree.strip_tags(_NON_CONTENT_TAGS)
    body = tree.body
    return _clean(body.text(separator=" ")) or "" if body is not None else ""


def parse_html(html: str, base_url: str) -> ParsedPage:
    """Parse leniently; malformed markup yields partial facts, never an exception."""
    tree = HTMLParser(html)
    meta = _meta_map(tree)
    images = tree.css("img")
    h1s = [t for n in tree.css("h1") if (t := _node_text(n))]
    links = _links(tree, base_url)
    canonical = _canonical(tree, base_url)
    lang = _lang(tree)
    title = _node_text(tree.css_first("title"))
    text = _visible_text(tree)  # mutates the tree, so it runs last
    return ParsedPage(
        title=title,
        meta_description=_clean(meta.get("description")),
        canonical=canonical,
        robots_meta=_clean(meta.get("robots")),
        og_title=_clean(meta.get("og:title")),
        og_description=_clean(meta.get("og:description")),
        lang=lang,
        h1s=h1s,
        text=text,
        word_count=len(text.split()),
        images_total=len(images),
        images_missing_alt=sum(1 for img in images if "alt" not in img.attributes),
        links=links,
    )
