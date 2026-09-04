import httpx
import respx

from seo_scout.crawler.robots import fetch_robots

UA = "SEOScout/0.1 (+https://github.com/Yataghanac/seo-scout)"


@respx.mock
async def test_disallow_crawl_delay_and_sitemaps(client: httpx.AsyncClient) -> None:
    respx.get("https://example.com/robots.txt").mock(
        return_value=httpx.Response(
            200,
            text="User-agent: *\nDisallow: /private/\nCrawl-delay: 2\n"
            "Sitemap: https://example.com/sm.xml\n",
        )
    )
    policy = await fetch_robots(client, "https://example.com/", UA)
    assert policy.allowed("https://example.com/public")
    assert not policy.allowed("https://example.com/private/x")
    assert policy.crawl_delay == 2.0
    assert policy.sitemaps == ["https://example.com/sm.xml"]


@respx.mock
async def test_wildcards_are_honoured(client: httpx.AsyncClient) -> None:
    respx.get("https://example.com/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /*.pdf$\n")
    )
    policy = await fetch_robots(client, "https://example.com/", UA)
    assert not policy.allowed("https://example.com/docs/x.pdf")
    assert policy.allowed("https://example.com/docs/x.pdfs")


@respx.mock
async def test_missing_robots_allows_everything(client: httpx.AsyncClient) -> None:
    respx.get("https://example.com/robots.txt").mock(return_value=httpx.Response(404))
    policy = await fetch_robots(client, "https://example.com/", UA)
    assert policy.allowed("https://example.com/anything")
    assert policy.crawl_delay is None
    assert policy.sitemaps == []


@respx.mock
async def test_server_error_disallows_everything(client: httpx.AsyncClient) -> None:
    respx.get("https://example.com/robots.txt").mock(return_value=httpx.Response(503))
    policy = await fetch_robots(client, "https://example.com/", UA)
    assert not policy.allowed("https://example.com/")


@respx.mock
async def test_network_error_disallows_everything(client: httpx.AsyncClient) -> None:
    respx.get("https://example.com/robots.txt").mock(side_effect=httpx.ConnectError("down"))
    policy = await fetch_robots(client, "https://example.com/", UA)
    assert not policy.allowed("https://example.com/")
