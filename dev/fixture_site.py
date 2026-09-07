"""A deliberately broken site, served over real HTTP, so every audit rule can fire.

The test suite mocks transport with respx. This exists to exercise the layer respx replaces:
real sockets, real redirect following, a real robots.txt and a real sitemap. Crawling it
fires 22 of the 25 rules; the remaining three (title_too_short, meta_description_missing,
meta_description_too_short) fire on ordinary sites.

    uv run python dev/fixture_site.py
    uv run seo-scout crawl http://127.0.0.1:8099/ --max-pages 30 --no-ai --delay 0 --db f.db

Not imported by anything and not part of the package: a tool you run by hand.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST, PORT = "127.0.0.1", 8099
BASE = f"http://{HOST}:{PORT}"
HTML = "text/html; charset=utf-8"

# One link per rule, all reachable from the home page.
LINKS = (
    "/long-title",
    "/no-title",
    "/long-meta",
    "/no-h1",
    "/two-h1",
    "/canonical-conflict",
    "/canonical-offsite",
    "/noindex",
    "/nofollow",
    "/x-robots",
    "/no-alt",
    "/broken",
    "/redirect-start",
    "/heavy",
    "/slow",
    "/no-lang",
)

GOOD_TITLE = "A perfectly reasonable title for a home page"
GOOD_META = (
    "This description is written to sit comfortably inside the seventy to one hundred "
    "and sixty character window that the audit asks for."
)


def doc(
    *,
    title: str | None = GOOD_TITLE,
    meta: str | None = GOOD_META,
    body: str = "<h1>Heading</h1><p>Some words.</p>",
    lang: str | None = "en",
    canonical: str | None = None,
    robots: str | None = None,
    og: bool = False,
) -> str:
    """One HTML page. Every argument set to None or omitted is a rule waiting to fire."""
    head = []
    if title is not None:
        head.append(f"<title>{title}</title>")
    if meta is not None:
        head.append(f'<meta name="description" content="{meta}">')
    if canonical is not None:
        head.append(f'<link rel="canonical" href="{canonical}">')
    if robots is not None:
        head.append(f'<meta name="robots" content="{robots}">')
    if og:
        head.append('<meta property="og:title" content="og">')
        head.append('<meta property="og:description" content="og">')
    lang_attr = f' lang="{lang}"' if lang else ""
    return (
        f"<!doctype html><html{lang_attr}><head><meta charset='utf-8'>"
        f"{''.join(head)}</head><body>{body}</body></html>"
    )


def _home() -> str:
    links = "".join(f'<a href="{href}">{href}</a> ' for href in LINKS)
    return doc(
        canonical=f"{BASE}/",
        og=True,
        body=f"<h1>Fixture site</h1><p>Every link below breaks a rule.</p><nav>{links}</nav>",
    )


PAGES: dict[str, str] = {
    "/": _home(),
    "/long-title": doc(
        title="A title that runs on well past the sixty character limit the audit sets for it"
    ),
    "/no-title": doc(title=None),
    "/long-meta": doc(
        meta="This description keeps going well past the one hundred and sixty character "
        "ceiling the audit sets, padding itself out with more clauses than any search "
        "result would ever show a reader on any device."
    ),
    "/no-h1": doc(body="<p>No heading here at all.</p>"),
    "/two-h1": doc(body="<h1>First</h1><h1>Second</h1><p>Two of them.</p>"),
    "/canonical-conflict": doc(canonical=f"{BASE}/somewhere-else"),
    "/canonical-offsite": doc(canonical="https://example.com/elsewhere"),
    "/noindex": doc(robots="noindex"),
    "/nofollow": doc(robots="nofollow"),
    "/x-robots": doc(body="<h1>Header-blocked</h1><p>See the response header.</p>"),
    "/no-alt": doc(body='<h1>Pictures</h1><img src="/i.png"><img src="/j.png" alt="fine">'),
    "/broken": doc(
        body='<h1>Broken</h1><a href="/missing">a link that 404s</a>'
        '<a href="/toobig">declares 6 MB</a><a href="/chunked-big">streams 6 MB</a>'
    ),
    "/redirect-end": doc(body="<h1>End of the chain</h1><p>Arrived.</p>"),
    "/slow": doc(body="<h1>Slow</h1><p>Took its time.</p>"),
    "/no-lang": doc(lang=None),
    "/somewhere-else": doc(body="<h1>Elsewhere</h1><p>Canonical target.</p>"),
    # Reachable only through the sitemap, so nothing links to it: orphan_page.
    "/orphan": doc(body="<h1>Orphan</h1><p>In the sitemap, linked from nowhere.</p>"),
}

REDIRECTS = {"/redirect-start": "/redirect-mid", "/redirect-mid": "/redirect-end"}

SITEMAP = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    + "".join(f"<url><loc>{BASE}{p}</loc></url>" for p in ("/", "/orphan", "/no-h1"))
    + "</urlset>"
)
ROBOTS = f"User-agent: *\nAllow: /\nSitemap: {BASE}/sitemap.xml\n"
HEAVY = doc(body="<h1>Heavy</h1><p>" + ("padding " * 400_000) + "</p>")
OVERSIZE = doc(body="<h1>Oversize</h1><p>" + ("x" * 6_000_000) + "</p>").encode()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        print(f"{self.command} {self.path}", flush=True)

    def _send(
        self, status: int, body: bytes, ctype: str, extra: dict[str, str] | None = None
    ) -> None:
        self.send_response(status)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(body)))
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, target: str) -> None:
        self.send_response(302)
        self.send_header("location", target)
        self.send_header("content-length", "0")
        self.end_headers()

    def _slow(self) -> None:
        time.sleep(2.5)  # over the 2000 ms slow_response threshold
        self._send(200, PAGES["/slow"].encode(), HTML)

    def _blocked_by_header(self) -> None:
        self._send(200, PAGES["/x-robots"].encode(), HTML, {"x-robots-tag": "noindex"})

    def _oversize_declared(self) -> None:
        """An honest content-length over the cap: refused before the body is read."""
        self._send(200, OVERSIZE, HTML)

    def _oversize_chunked(self) -> None:
        """No content-length at all, so only the streaming check can stop this one."""
        self.send_response(200)
        self.send_header("content-type", HTML)
        self.send_header("transfer-encoding", "chunked")
        self.end_headers()
        block = b"<p>" + b"x" * 65_536 + b"</p>"
        for _ in range(96):  # ~6 MB
            self.wfile.write(f"{len(block):X}".encode() + b"\r\n" + block + b"\r\n")
        self.wfile.write(b"0\r\n\r\n")

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        special: dict[str, Callable[[], None]] = {
            "/robots.txt": lambda: self._send(200, ROBOTS.encode(), "text/plain; charset=utf-8"),
            "/sitemap.xml": lambda: self._send(200, SITEMAP.encode(), "application/xml"),
            "/heavy": lambda: self._send(200, HEAVY.encode(), HTML),
            "/slow": self._slow,
            "/x-robots": self._blocked_by_header,
            "/toobig": self._oversize_declared,
            "/chunked-big": self._oversize_chunked,
        }
        if handler := special.get(path):
            handler()
        elif path in REDIRECTS:
            self._redirect(REDIRECTS[path])
        elif path in PAGES:
            self._send(200, PAGES[path].encode(), HTML)
        else:
            self._send(404, b"<h1>404</h1>", HTML)


if __name__ == "__main__":
    print(f"fixture site on {BASE}", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
