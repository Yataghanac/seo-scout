import pytest

from seo_scout import urls
from seo_scout.parse import parse_html

HTML = """<html lang="en"><head>
<title> Hello   World </title>
<meta name="description" content="Desc here">
<link rel="canonical" href="/canon">
<meta name="robots" content="noindex, nofollow">
<meta property="og:title" content="OG T">
<meta property="og:description" content="OG D">
</head><body>
<h1>One</h1><h1>Two</h1>
<p>some visible words here</p>
<script>var hidden = "not words";</script><style>.a{color:red}</style>
<a href="/a">A</a> <a href="https://other.com/">O</a> <a href="mailto:x@y.z">M</a>
<a href="/a#frag">A again</a>
<img src="a.png"><img src="b.png" alt="B"><img src="c.png" alt="">
</body></html>"""


def test_head_fields() -> None:
    p = parse_html(HTML, "https://example.com/dir/")
    assert p.title == "Hello World"
    assert p.meta_description == "Desc here"
    assert p.canonical == "https://example.com/canon"
    assert p.robots_meta == "noindex, nofollow"
    assert p.og_title == "OG T"
    assert p.og_description == "OG D"
    assert p.lang == "en"


def test_body_fields() -> None:
    p = parse_html(HTML, "https://example.com/dir/")
    assert p.h1s == ["One", "Two"]
    assert p.word_count == 11
    assert "hidden" not in p.text
    assert p.images_total == 3
    assert p.images_missing_alt == 1  # alt="" is a deliberate decorative image


def test_links_are_resolved_deduped_and_filtered() -> None:
    p = parse_html(HTML, "https://example.com/dir/")
    assert [link.url for link in p.links] == ["https://example.com/a", "https://other.com/"]


def test_links_carry_identity_key_and_request_spelling() -> None:
    """The key is what the audit and frontier match on; the url is what gets fetched."""
    html = '<a href="/a/">1</a><a href="/a">2</a><a href="/b?y=2&x=1">3</a>'
    p = parse_html(html, "https://example.com/")
    assert [(link.key, link.url) for link in p.links] == [
        ("https://example.com/a", "https://example.com/a/"),
        ("https://example.com/b?x=1&y=2", "https://example.com/b?y=2&x=1"),
    ]


def test_each_anchor_is_parsed_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """150k links took ~6.5 s when every anchor was split three or four times."""
    real, calls = urls.urlsplit, []
    monkeypatch.setattr(urls, "urlsplit", lambda value: (calls.append(value), real(value))[1])
    anchors = "".join(f'<a href="/p{i}">x</a>' for i in range(20))
    parse_html(f"<html><body>{anchors}</body></html>", "https://example.com/")
    assert len(calls) <= 20


def test_malformed_html_does_not_raise() -> None:
    p = parse_html(
        "<html><title>Broken</title><body><p>x <a href='/y'>y</div></p><b>", "https://e.com/"
    )
    assert p.title == "Broken"
    assert [link.url for link in p.links] == ["https://e.com/y"]


def test_empty_document() -> None:
    p = parse_html("", "https://e.com/")
    assert p.title is None
    assert p.word_count == 0
    assert p.links == []
    assert p.h1s == []


def test_missing_optional_fields_are_none() -> None:
    p = parse_html("<html><body><p>hi</p></body></html>", "https://e.com/")
    assert p.meta_description is None
    assert p.canonical is None
    assert p.lang is None
    assert p.og_title is None


def test_malformed_ipv6_anchor_is_dropped_not_raised() -> None:
    p = parse_html('<a href="https://[::1/x">x</a><a href="/ok">y</a>', "https://e.com/")
    assert [link.url for link in p.links] == ["https://e.com/ok"]
