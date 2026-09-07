import logging

import httpx
import pytest
import respx

from seo_scout.crawler.sitemap import discover_seeds

URLSET = """<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://example.com/a</loc></url><url><loc>https://example.com/b/</loc></url>
<url><loc>https://other.com/off</loc></url></urlset>"""


@respx.mock
async def test_reads_urlset_and_always_includes_homepage(client: httpx.AsyncClient) -> None:
    respx.get("https://example.com/sitemap.xml").mock(return_value=httpx.Response(200, text=URLSET))
    seeds = await discover_seeds(client, "https://example.com/", [])
    assert seeds.urls == ["https://example.com/", "https://example.com/a", "https://example.com/b/"]
    assert seeds.from_sitemap == {"https://example.com/a", "https://example.com/b"}


@respx.mock
async def test_follows_sitemap_index(client: httpx.AsyncClient) -> None:
    index = """<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
    <sitemap><loc>https://example.com/child.xml</loc></sitemap></sitemapindex>"""
    respx.get("https://example.com/sitemap.xml").mock(return_value=httpx.Response(200, text=index))
    respx.get("https://example.com/child.xml").mock(return_value=httpx.Response(200, text=URLSET))
    seeds = await discover_seeds(client, "https://example.com/", [])
    assert "https://example.com/a" in seeds.from_sitemap


@respx.mock
async def test_missing_sitemap_falls_back_to_homepage(client: httpx.AsyncClient) -> None:
    respx.get("https://example.com/sitemap.xml").mock(return_value=httpx.Response(404))
    seeds = await discover_seeds(client, "https://example.com/", [])
    assert seeds.urls == ["https://example.com/"]
    assert seeds.from_sitemap == set()


@respx.mock
async def test_empty_and_malformed_sitemaps_fall_back(client: httpx.AsyncClient) -> None:
    respx.get("https://example.com/sitemap.xml").mock(
        return_value=httpx.Response(200, text="<urlset></urlset")
    )
    seeds = await discover_seeds(client, "https://example.com/", [])
    assert seeds.urls == ["https://example.com/"]


@respx.mock
async def test_robots_sitemaps_take_priority(client: httpx.AsyncClient) -> None:
    default = respx.get("https://example.com/sitemap.xml").mock(return_value=httpx.Response(404))
    respx.get("https://example.com/sm.xml").mock(return_value=httpx.Response(200, text=URLSET))
    seeds = await discover_seeds(client, "https://example.com/", ["https://example.com/sm.xml"])
    assert "https://example.com/a" in seeds.from_sitemap
    assert not default.called


@respx.mock
async def test_network_error_falls_back_to_homepage(client: httpx.AsyncClient) -> None:
    respx.get("https://example.com/sitemap.xml").mock(side_effect=httpx.ReadTimeout("slow"))
    seeds = await discover_seeds(client, "https://example.com/", [])
    assert seeds.urls == ["https://example.com/"]


@respx.mock
async def test_site_under_a_path_prefix_tries_its_own_sitemap(client: httpx.AsyncClient) -> None:
    """docs.example.com/uv/ keeps its sitemap at /uv/sitemap.xml, not at the host root."""
    docs = """<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
    <url><loc>https://example.com/docs/guide/</loc></url></urlset>"""
    root = respx.get("https://example.com/sitemap.xml").mock(return_value=httpx.Response(404))
    respx.get("https://example.com/docs/sitemap.xml").mock(
        return_value=httpx.Response(200, text=docs)
    )
    seeds = await discover_seeds(client, "https://example.com/docs/", [])
    assert not root.called  # the host root would seed every sibling site on the host
    assert seeds.urls == ["https://example.com/docs/", "https://example.com/docs/guide/"]
    assert seeds.from_sitemap == {"https://example.com/docs/guide"}


DOCS = """<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://example.com/docs/guide/</loc></url></urlset>"""


@respx.mock
async def test_query_string_on_the_start_url_does_not_collapse_the_prefix(
    client: httpx.AsyncClient,
) -> None:
    respx.get("https://example.com/sitemap.xml").mock(return_value=httpx.Response(404))
    docs = respx.get("https://example.com/docs/sitemap.xml").mock(
        return_value=httpx.Response(200, text=DOCS)
    )
    seeds = await discover_seeds(client, "https://example.com/docs?lang=en", [])
    assert docs.called
    assert "https://example.com/docs/guide" in seeds.from_sitemap


@respx.mock
async def test_dotted_directory_prefix_is_still_tried(client: httpx.AsyncClient) -> None:
    versioned = """<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
    <url><loc>https://example.com/3.12/lib/</loc></url></urlset>"""
    respx.get("https://example.com/sitemap.xml").mock(return_value=httpx.Response(404))
    respx.get("https://example.com/3.12/sitemap.xml").mock(
        return_value=httpx.Response(200, text=versioned)
    )
    seeds = await discover_seeds(client, "https://example.com/3.12/", [])
    assert "https://example.com/3.12/lib" in seeds.from_sitemap


@respx.mock
async def test_prefix_candidate_is_tried_even_when_robots_names_a_sitemap(
    client: httpx.AsyncClient,
) -> None:
    root = respx.get("https://example.com/sitemap.xml").mock(return_value=httpx.Response(404))
    respx.get("https://example.com/main.xml").mock(return_value=httpx.Response(200, text=URLSET))
    respx.get("https://example.com/docs/sitemap.xml").mock(
        return_value=httpx.Response(200, text=DOCS)
    )
    seeds = await discover_seeds(
        client, "https://example.com/docs/", ["https://example.com/main.xml"]
    )
    assert not root.called
    assert {"https://example.com/a", "https://example.com/docs/guide"} <= seeds.from_sitemap


@respx.mock
async def test_prefix_guess_walks_up_to_parent_paths(client: httpx.AsyncClient) -> None:
    """Starting at /uv/guides/ must still find /uv/sitemap.xml."""
    respx.get("https://example.com/sitemap.xml").mock(return_value=httpx.Response(404))
    respx.get("https://example.com/uv/guides/sitemap.xml").mock(return_value=httpx.Response(404))
    respx.get("https://example.com/uv/sitemap.xml").mock(
        return_value=httpx.Response(200, text=DOCS)
    )
    seeds = await discover_seeds(client, "https://example.com/uv/guides/", [])
    assert "https://example.com/docs/guide" in seeds.from_sitemap


@respx.mock
async def test_a_trailing_file_segment_costs_one_miss_then_tries_its_directory(
    client: httpx.AsyncClient,
) -> None:
    """No file-versus-directory guessing: /3.12 and /index.html look alike; the bound pays."""
    respx.get("https://example.com/sitemap.xml").mock(return_value=httpx.Response(404))
    respx.get("https://example.com/docs/index.html/sitemap.xml").mock(
        return_value=httpx.Response(404)
    )
    respx.get("https://example.com/docs/sitemap.xml").mock(
        return_value=httpx.Response(200, text=DOCS)
    )
    seeds = await discover_seeds(client, "https://example.com/docs/index.html", [])
    assert "https://example.com/docs/guide" in seeds.from_sitemap


@respx.mock
async def test_dotted_directory_without_a_trailing_slash_is_still_tried(
    client: httpx.AsyncClient,
) -> None:
    versioned = """<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
    <url><loc>https://example.com/3.12/lib/</loc></url></urlset>"""
    respx.get("https://example.com/sitemap.xml").mock(return_value=httpx.Response(404))
    respx.get("https://example.com/3.12/sitemap.xml").mock(
        return_value=httpx.Response(200, text=versioned)
    )
    seeds = await discover_seeds(client, "https://example.com/3.12", [])
    assert "https://example.com/3.12/lib" in seeds.from_sitemap


@respx.mock
async def test_prefix_walk_stops_at_the_first_hit(client: httpx.AsyncClient) -> None:
    respx.get("https://example.com/sitemap.xml").mock(return_value=httpx.Response(404))
    respx.get("https://example.com/a/b/sitemap.xml").mock(
        return_value=httpx.Response(200, text=DOCS)
    )
    parent = respx.get("https://example.com/a/sitemap.xml").mock(
        return_value=httpx.Response(200, text=URLSET)
    )
    seeds = await discover_seeds(client, "https://example.com/a/b/", [])
    assert not parent.called
    assert "https://example.com/docs/guide" in seeds.from_sitemap


@respx.mock
async def test_prefix_walk_is_bounded(client: httpx.AsyncClient) -> None:
    respx.get("https://example.com/sitemap.xml").mock(return_value=httpx.Response(404))
    for prefix in ("/a/b/c/d/e", "/a/b/c/d", "/a/b/c"):
        respx.get(f"https://example.com{prefix}/sitemap.xml").mock(return_value=httpx.Response(404))
    deep = respx.get("https://example.com/a/b/sitemap.xml").mock(
        return_value=httpx.Response(200, text=DOCS)
    )
    seeds = await discover_seeds(client, "https://example.com/a/b/c/d/e/", [])
    assert not deep.called
    assert seeds.urls == ["https://example.com/a/b/c/d/e/"]


@respx.mock
async def test_prefix_sitemap_is_read_before_a_large_robots_index(
    client: httpx.AsyncClient,
) -> None:
    """The file cap must never starve the sitemap of the site the user asked for."""
    children = "".join(
        f"<sitemap><loc>https://example.com/child{i}.xml</loc></sitemap>" for i in range(60)
    )
    index = f'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{children}</sitemapindex>'
    respx.get("https://example.com/index.xml").mock(return_value=httpx.Response(200, text=index))
    respx.route(url__regex=r"https://example\.com/child\d+\.xml").mock(
        return_value=httpx.Response(200, text=URLSET)
    )
    docs = respx.get("https://example.com/docs/sitemap.xml").mock(
        return_value=httpx.Response(200, text=DOCS)
    )
    seeds = await discover_seeds(
        client, "https://example.com/docs/", ["https://example.com/index.xml"]
    )
    assert docs.called
    assert seeds.urls[:2] == ["https://example.com/docs/", "https://example.com/docs/guide/"]


@respx.mock
async def test_walk_stops_at_a_guess_that_robots_already_named(client: httpx.AsyncClient) -> None:
    respx.get("https://example.com/uv/guides/sitemap.xml").mock(
        return_value=httpx.Response(200, text=DOCS)
    )
    parent = respx.get("https://example.com/uv/sitemap.xml").mock(
        return_value=httpx.Response(200, text=URLSET)
    )
    seeds = await discover_seeds(
        client, "https://example.com/uv/guides/", ["https://example.com/uv/guides/sitemap.xml"]
    )
    assert not parent.called
    assert seeds.from_sitemap == {"https://example.com/docs/guide"}


@respx.mock
async def test_soft_404_does_not_stop_the_walk(client: httpx.AsyncClient) -> None:
    """A host that answers 200 with an HTML shell for every path has not served a sitemap."""
    respx.get("https://example.com/sitemap.xml").mock(return_value=httpx.Response(404))
    respx.get("https://example.com/uv/guides/sitemap.xml").mock(
        return_value=httpx.Response(200, html="<html><body>Not found</body></html>")
    )
    respx.get("https://example.com/uv/sitemap.xml").mock(
        return_value=httpx.Response(200, text=DOCS)
    )
    seeds = await discover_seeds(client, "https://example.com/uv/guides/", [])
    assert "https://example.com/docs/guide" in seeds.from_sitemap


@respx.mock
async def test_seeds_keep_identity_key_and_spelling_together(client: httpx.AsyncClient) -> None:
    respx.get("https://example.com/sitemap.xml").mock(return_value=httpx.Response(200, text=URLSET))
    seeds = await discover_seeds(client, "https://Example.com", [])
    assert seeds.pairs == [
        ("https://example.com/", "https://example.com/"),
        ("https://example.com/a", "https://example.com/a"),
        ("https://example.com/b", "https://example.com/b/"),
    ]


@respx.mock
async def test_a_prefix_index_cannot_starve_the_robots_sitemaps(client: httpx.AsyncClient) -> None:
    """Each source has its own file budget, so neither can use up the other's."""
    children = "".join(
        f"<sitemap><loc>https://example.com/docs/child{i}.xml</loc></sitemap>" for i in range(60)
    )
    index = f'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{children}</sitemapindex>'
    respx.get("https://example.com/docs/sitemap.xml").mock(
        return_value=httpx.Response(200, text=index)
    )
    respx.route(url__regex=r"https://example\.com/docs/child\d+\.xml").mock(
        return_value=httpx.Response(200, text=DOCS)
    )
    main = respx.get("https://example.com/main.xml").mock(
        return_value=httpx.Response(200, text=URLSET)
    )
    seeds = await discover_seeds(
        client, "https://example.com/docs/", ["https://example.com/main.xml"]
    )
    assert main.called
    assert "https://example.com/a" in seeds.from_sitemap


@respx.mock
async def test_malformed_index_children_are_skipped(client: httpx.AsyncClient) -> None:
    index = """<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
    <sitemap><loc>https://[::1/x</loc></sitemap>
    <sitemap><loc>https://example.com:abc/x.xml</loc></sitemap>
    <sitemap><loc>https://example.com/child.xml</loc></sitemap></sitemapindex>"""
    respx.get("https://example.com/sitemap.xml").mock(return_value=httpx.Response(200, text=index))
    respx.get("https://example.com/child.xml").mock(return_value=httpx.Response(200, text=URLSET))
    seeds = await discover_seeds(client, "https://example.com/", [])
    assert "https://example.com/a" in seeds.from_sitemap


@respx.mock
async def test_a_robots_sitemap_line_httpx_cannot_request_is_skipped(
    client: httpx.AsyncClient,
) -> None:
    seeds = await discover_seeds(
        client,
        "https://example.com/",
        ["https://example.com:abc/sm.xml", "https://xn--a.com/s.xml"],
    )
    assert seeds.urls == ["https://example.com/"]


@respx.mock
async def test_counts_the_sitemaps_that_parsed_not_the_locations_tried(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    """A site with no sitemap answers three 404s; it did not hand us three sitemaps."""
    respx.get(url__regex=r"https://example\.com/.*").mock(return_value=httpx.Response(404))
    with caplog.at_level(logging.INFO, logger="seo_scout.crawler.sitemap"):
        seeds = await discover_seeds(client, "https://example.com/docs/guide/", [])
    assert seeds.urls == ["https://example.com/docs/guide/"]
    record = caplog.records[-1]
    assert record.sitemap_files == 0
    assert record.sitemap_fetches == 3


@respx.mock
async def test_counts_only_the_file_that_answered(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    respx.get("https://example.com/docs/guide/sitemap.xml").mock(return_value=httpx.Response(404))
    respx.get("https://example.com/docs/sitemap.xml").mock(return_value=httpx.Response(404))
    respx.get("https://example.com/sm.xml").mock(return_value=httpx.Response(200, text=URLSET))
    with caplog.at_level(logging.INFO, logger="seo_scout.crawler.sitemap"):
        await discover_seeds(
            client, "https://example.com/docs/guide/", ["https://example.com/sm.xml"]
        )
    record = caplog.records[-1]
    assert record.sitemap_files == 1
    assert record.sitemap_fetches == 3
