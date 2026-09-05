"""Pure URL helpers shared by the crawler and the audit rules. No I/O."""

from __future__ import annotations

from urllib.parse import SplitResult, parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import tldextract

# suffix_list_urls=() disables the network fetch; the bundled snapshot is used instead.
_extract = tldextract.TLDExtract(suffix_list_urls=(), fallback_to_snapshot=True)

_SKIP_PREFIXES = ("mailto:", "tel:", "javascript:", "data:", "#")
_DEFAULT_PORTS = {"http": 80, "https": 443}
_BINARY_EXTENSIONS = frozenset(
    {
        ".pdf", ".zip", ".gz", ".tar", ".rar", ".7z", ".exe", ".dmg", ".msi",
        ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".bmp", ".tiff",
        ".mp3", ".mp4", ".avi", ".mov", ".wmv", ".flv", ".webm", ".ogg", ".wav",
        ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".csv",
        ".css", ".js", ".json", ".xml", ".woff", ".woff2", ".ttf", ".eot",
    }
)  # fmt: skip


def _netloc(parts: SplitResult, scheme: str) -> str | None:
    """Host (bracketed when IPv6) plus a non-default port. Credentials never survive:

    httpx would send them as Basic auth and the URL would be stored with them.
    """
    host, port = parts.hostname, parts.port  # both raise ValueError on garbage
    if not host:
        return None
    if ":" in host:
        host = f"[{host}]"
    return host if port in (None, _DEFAULT_PORTS[scheme]) else f"{host}:{port}"


def link_pair(raw: str, base: str | None = None) -> tuple[str, str] | None:
    """(identity key, request spelling) from one parse, or None if never to be followed.

    The key answers "is this the same page?" (scheme and host lowercased, default port
    and fragment dropped, query sorted, trailing slashes collapsed). The request answers
    "what do I fetch?": the same netloc, but path and query exactly as written. Requesting
    the key manufactures redirects on sites that canonicalise with a trailing slash, so
    the two must stay separate; computing both from one split keeps link parsing cheap.
    """
    raw = raw.strip()
    if not raw or raw.lower().startswith(_SKIP_PREFIXES):
        return None
    joined = urljoin(base, raw) if base else raw
    try:
        parts = urlsplit(joined)
        scheme = parts.scheme.lower()
        netloc = _netloc(parts, scheme) if scheme in _DEFAULT_PORTS else None
    except ValueError:
        return None
    if netloc is None:
        return None
    request = urlunsplit((scheme, netloc, parts.path or "/", parts.query, ""))
    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"
    query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True)))
    return urlunsplit((scheme, netloc, path, query, "")), request


def normalize(raw: str, base: str | None = None) -> str | None:
    """A page's identity: the key half of `link_pair()`."""
    pair = link_pair(raw, base)
    return pair[0] if pair else None


def resolve(raw: str, base: str | None = None) -> str | None:
    """The URL to request: the spelling half of `link_pair()`."""
    pair = link_pair(raw, base)
    return pair[1] if pair else None


def registrable_domain(url: str) -> str:
    """`blog.example.co.uk` -> `example.co.uk`; falls back to the hostname (localhost)."""
    host = urlsplit(url).hostname or ""
    ext = _extract(host)
    if ext.domain and ext.suffix:
        return f"{ext.domain}.{ext.suffix}".lower()
    return host.lower()


def same_site(a: str, b: str) -> bool:
    """True when both URLs share a registrable domain (scheme and subdomain ignored)."""
    return registrable_domain(a) == registrable_domain(b) != ""


def looks_binary(url: str) -> bool:
    """Extension suggests a non-HTML asset, so a HEAD request should go first."""
    path = urlsplit(url).path.lower()
    dot = path.rfind(".")
    return dot != -1 and path[dot:] in _BINARY_EXTENSIONS
