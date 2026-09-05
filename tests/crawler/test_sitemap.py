import httpx
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
    assert root.called
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
async def test_prefix_guess_skips_a_trailing_file_segment(client: httpx.AsyncClient) -> None:
    """/docs/index.html lives in /docs/, so the guess is /docs/sitemap.xml, not under the file."""
    respx.get("https://example.com/sitemap.xml").mock(return_value=httpx.Response(404))
    under_file = respx.get("https://example.com/docs/index.html/sitemap.xml").mock(
        return_value=httpx.Response(404)
    )
    respx.get("https://example.com/docs/sitemap.xml").mock(
        return_value=httpx.Response(200, text=DOCS)
    )
    seeds = await discover_seeds(client, "https://example.com/docs/index.html", [])
    assert not under_file.called
    assert "https://example.com/docs/guide" in seeds.from_sitemap


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
