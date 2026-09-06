import pytest

from seo_scout import urls
from seo_scout.urls import link_pair, looks_binary, normalize, resolve, same_site


@pytest.mark.parametrize(
    ("raw", "base", "expected"),
    [
        ("https://Example.com/A", None, "https://example.com/A"),
        ("https://example.com/a#frag", None, "https://example.com/a"),
        ("https://example.com/a?b=2&a=1", None, "https://example.com/a?a=1&b=2"),
        ("/rel/path", "https://example.com/dir/", "https://example.com/rel/path"),
        ("sub", "https://example.com/dir/", "https://example.com/dir/sub"),
        ("https://example.com/a/", None, "https://example.com/a"),
        ("https://example.com/a///", None, "https://example.com/a"),
        ("https://example.com", None, "https://example.com/"),
        ("https://example.com/", None, "https://example.com/"),
        ("HTTPS://example.com:443/x", None, "https://example.com/x"),
        ("http://example.com:8080/x", None, "http://example.com:8080/x"),
        ("  https://example.com/x  ", None, "https://example.com/x"),
    ],
)
def test_normalize(raw: str, base: str | None, expected: str) -> None:
    assert normalize(raw, base) == expected


@pytest.mark.parametrize(
    "raw",
    ["mailto:a@b.c", "tel:+123", "javascript:void(0)", "#top", "", "ftp://x.y/z", "data:,hi"],
)
def test_normalize_rejects_non_crawlable(raw: str) -> None:
    assert normalize(raw, "https://example.com/") is None


def test_relative_without_base_is_rejected() -> None:
    assert normalize("/a") is None


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("https://example.com/", "https://blog.example.com/x", True),
        ("https://a.co.uk/", "https://www.a.co.uk/", True),
        ("https://a.co.uk/", "https://b.co.uk/", False),
        ("https://example.com/", "https://example.org/", False),
        ("https://example.com/", "http://example.com/", True),
        ("https://localhost:8000/", "https://localhost:8000/x", True),
    ],
)
def test_same_site(a: str, b: str, expected: bool) -> None:
    assert same_site(a, b) is expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://x.test/a.pdf", True),
        ("https://x.test/a.JPG", True),
        ("https://x.test/a.zip?dl=1", True),
        ("https://x.test/page", False),
        ("https://x.test/page.html", False),
        ("https://x.test/x?y=z", False),
    ],
)
def test_looks_binary(url: str, expected: bool) -> None:
    assert looks_binary(url) is expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://user:pw@example.com/a/", "https://example.com/a/"),  # never send Basic auth
        ("HTTPS://Example.com:443/A/", "https://example.com/A/"),
        ("http://example.com:8080/x/", "http://example.com:8080/x/"),
        ("http://[::1]:8000/x/", "http://[::1]:8000/x/"),
        ("https://example.com/a?b=2&a=1#frag", "https://example.com/a?b=2&a=1"),
    ],
)
def test_resolve_rebuilds_the_netloc_but_keeps_the_spelling(raw: str, expected: str) -> None:
    assert resolve(raw) == expected


def test_normalize_keeps_ipv6_brackets() -> None:
    assert normalize("http://[::1]:8000/x/") == "http://[::1]:8000/x"


def test_link_pair_returns_identity_key_and_request_spelling() -> None:
    assert link_pair("/a/?b=2&a=1#f", "https://example.com/") == (
        "https://example.com/a?a=1&b=2",
        "https://example.com/a/?b=2&a=1",
    )
    assert link_pair("mailto:a@b.c", "https://example.com/") is None
    assert link_pair("/a", None) is None


@pytest.mark.parametrize("raw", ["https://[::1/x", "//[::1/x", "https://e.com:abc/x"])
def test_link_pair_rejects_a_malformed_host_instead_of_raising(raw: str) -> None:
    """One hostile anchor must never take a crawl down."""
    assert link_pair(raw, "https://e.com/") is None


def test_registrable_domain_is_computed_once_per_host() -> None:
    """same_site runs per link; the suffix-list lookup must run once per host."""
    urls._domain_of.cache_clear()
    for i in range(5):
        assert same_site("https://once-a.com/", f"https://sub{i % 2}.once-a.com/p{i}")
    info = urls._domain_of.cache_info()
    assert (info.misses, info.hits) == (3, 7)  # 3 hosts, 10 lookups


@pytest.mark.parametrize("raw", ["https://[::1/x", "not a url", ""])
def test_same_site_never_raises_for_what_the_parser_rejects(raw: str) -> None:
    """A canonical or sitemap entry is untrusted text; the audit and discovery call this."""
    assert not same_site("https://e.com/", raw)
