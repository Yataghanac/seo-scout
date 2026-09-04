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
    assert seeds.urls == ["https://example.com/", "https://example.com/a", "https://example.com/b"]
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
