import asyncio
import sqlite3
from typing import Any

import httpx
import pytest
import respx

from seo_scout.config import Settings
from seo_scout.crawler import crawler as crawler_module
from seo_scout.crawler.crawler import Crawler
from seo_scout.store import repo_pages, repo_runs


class Sleeps:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def make(
    conn: sqlite3.Connection,
    client: httpx.AsyncClient,
    sleeps: Sleeps | None = None,
    **kw: Any,
) -> Crawler:
    settings = Settings(min_delay=0.0, **kw)
    return Crawler(settings=settings, conn=conn, client=client, sleep=sleeps or Sleeps())


def html(*links: str, body: str = "") -> str:
    anchors = "".join(f'<a href="{link}">l</a>' for link in links)
    return f"<html><head><title>t</title></head><body>{anchors}{body}</body></html>"


def no_robots_no_sitemap() -> None:
    respx.get("https://e.com/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://e.com/sitemap.xml").mock(return_value=httpx.Response(404))


def urls(conn: sqlite3.Connection, run_id: int) -> dict[str, int]:
    return {p.url: p.depth for p in repo_pages.list_pages(conn, run_id)}


@respx.mock
async def test_bfs_crawl_persists_pages_and_links(
    conn: sqlite3.Connection, client: httpx.AsyncClient
) -> None:
    no_robots_no_sitemap()
    respx.get("https://e.com/").mock(return_value=httpx.Response(200, html=html("/a", "/b")))
    respx.get("https://e.com/a").mock(return_value=httpx.Response(200, html=html("/c", "/")))
    respx.get("https://e.com/b").mock(return_value=httpx.Response(200, html=html()))
    respx.get("https://e.com/c").mock(return_value=httpx.Response(200, html=html()))
    report = await make(conn, client).run("https://e.com")
    assert report.status == "complete"
    assert report.pages == 4
    assert urls(conn, report.run_id) == {
        "https://e.com/": 0,
        "https://e.com/a": 1,
        "https://e.com/b": 1,
        "https://e.com/c": 2,
    }
    assert repo_pages.inbound_counts(conn, report.run_id)["https://e.com/c"] == 1
    run = repo_runs.get_run(conn, report.run_id)
    assert run is not None
    assert run.status == "complete"


@respx.mock
async def test_robots_disallow_is_honoured(
    conn: sqlite3.Connection, client: httpx.AsyncClient
) -> None:
    respx.get("https://e.com/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /private\n")
    )
    respx.get("https://e.com/sitemap.xml").mock(return_value=httpx.Response(404))
    respx.get("https://e.com/").mock(
        return_value=httpx.Response(200, html=html("/private/x", "/ok"))
    )
    private = respx.get("https://e.com/private/x").mock(
        return_value=httpx.Response(200, html=html())
    )
    respx.get("https://e.com/ok").mock(return_value=httpx.Response(200, html=html()))
    report = await make(conn, client).run("https://e.com/")
    assert not private.called
    assert set(urls(conn, report.run_id)) == {"https://e.com/", "https://e.com/ok"}


@respx.mock
async def test_crawl_delay_from_robots_is_used(
    conn: sqlite3.Connection, client: httpx.AsyncClient
) -> None:
    respx.get("https://e.com/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nCrawl-delay: 2\n")
    )
    respx.get("https://e.com/sitemap.xml").mock(return_value=httpx.Response(404))
    respx.get("https://e.com/").mock(return_value=httpx.Response(200, html=html("/a")))
    respx.get("https://e.com/a").mock(return_value=httpx.Response(200, html=html()))
    sleeps = Sleeps()
    await make(conn, client, sleeps).run("https://e.com/")
    assert any(abs(s - 2.0) < 0.05 for s in sleeps.calls)


@respx.mock
async def test_off_domain_links_are_never_fetched(
    conn: sqlite3.Connection, client: httpx.AsyncClient
) -> None:
    no_robots_no_sitemap()
    respx.get("https://e.com/").mock(
        return_value=httpx.Response(200, html=html("https://other.com/x", "mailto:a@b.c", "/in"))
    )
    other = respx.get("https://other.com/x").mock(return_value=httpx.Response(200, html=html()))
    respx.get("https://e.com/in").mock(return_value=httpx.Response(200, html=html()))
    report = await make(conn, client).run("https://e.com/")
    assert not other.called
    assert set(urls(conn, report.run_id)) == {"https://e.com/", "https://e.com/in"}


@respx.mock
async def test_depth_cap(conn: sqlite3.Connection, client: httpx.AsyncClient) -> None:
    no_robots_no_sitemap()
    respx.get("https://e.com/").mock(return_value=httpx.Response(200, html=html("/1")))
    respx.get("https://e.com/1").mock(return_value=httpx.Response(200, html=html("/2")))
    deep = respx.get("https://e.com/2").mock(return_value=httpx.Response(200, html=html()))
    report = await make(conn, client).run("https://e.com/", max_depth=1)
    assert not deep.called
    assert report.pages == 2


@respx.mock
async def test_max_pages_cap(conn: sqlite3.Connection, client: httpx.AsyncClient) -> None:
    no_robots_no_sitemap()
    respx.get("https://e.com/").mock(return_value=httpx.Response(200, html=html("/a", "/b", "/c")))
    for p in "abc":
        respx.get(f"https://e.com/{p}").mock(return_value=httpx.Response(200, html=html()))
    report = await make(conn, client).run("https://e.com/", max_pages=2)
    assert report.pages == 2


@respx.mock
async def test_self_linking_loop_terminates(
    conn: sqlite3.Connection, client: httpx.AsyncClient
) -> None:
    no_robots_no_sitemap()
    respx.get("https://e.com/").mock(
        return_value=httpx.Response(200, html=html("/", "/#top", "/?", "/a"))
    )
    respx.get("https://e.com/a").mock(return_value=httpx.Response(200, html=html("/a", "/")))
    report = await make(conn, client).run("https://e.com/")
    assert report.pages == 2


@respx.mock
async def test_redirect_chain_is_persisted(
    conn: sqlite3.Connection, client: httpx.AsyncClient
) -> None:
    no_robots_no_sitemap()
    respx.get("https://e.com/").mock(return_value=httpx.Response(200, html=html("/old")))
    respx.get("https://e.com/old").mock(
        return_value=httpx.Response(301, headers={"location": "/new"})
    )
    respx.get("https://e.com/new").mock(return_value=httpx.Response(200, html=html()))
    report = await make(conn, client).run("https://e.com/")
    pages = {p.url: p for p in repo_pages.list_pages(conn, report.run_id)}
    old = pages["https://e.com/old"]
    assert old.final_url == "https://e.com/new"
    assert [h.status for h in old.redirect_chain] == [301]
    assert old.status == 200


@respx.mock
async def test_malformed_html_and_404s_do_not_crash(
    conn: sqlite3.Connection, client: httpx.AsyncClient
) -> None:
    no_robots_no_sitemap()
    respx.get("https://e.com/").mock(
        return_value=httpx.Response(200, html="<html><a href='/x'><p><<<>>>")
    )
    respx.get("https://e.com/x").mock(return_value=httpx.Response(404, html="nope"))
    report = await make(conn, client).run("https://e.com/")
    assert report.status == "complete"
    assert repo_pages.status_by_url(conn, report.run_id)["https://e.com/x"] == 404


@respx.mock
async def test_single_network_failure_is_recorded_and_crawl_continues(
    conn: sqlite3.Connection, client: httpx.AsyncClient
) -> None:
    no_robots_no_sitemap()
    respx.get("https://e.com/").mock(return_value=httpx.Response(200, html=html("/a", "/b")))
    respx.get("https://e.com/a").mock(side_effect=httpx.ConnectError("down"))
    respx.get("https://e.com/b").mock(return_value=httpx.Response(200, html=html()))
    report = await make(conn, client).run("https://e.com/")
    assert report.status == "complete"
    statuses = repo_pages.status_by_url(conn, report.run_id)
    assert statuses["https://e.com/a"] == 0
    assert statuses["https://e.com/b"] == 200


@respx.mock
async def test_network_going_down_yields_partial_run(
    conn: sqlite3.Connection, client: httpx.AsyncClient
) -> None:
    no_robots_no_sitemap()
    respx.get("https://e.com/").mock(
        return_value=httpx.Response(200, html=html("/a", "/b", "/c", "/d"))
    )
    for p in "abcd":
        respx.get(f"https://e.com/{p}").mock(side_effect=httpx.ConnectError("down"))
    report = await make(conn, client).run("https://e.com/")
    assert report.status == "partial"
    assert "https://e.com/" in urls(conn, report.run_id)
    run = repo_runs.get_run(conn, report.run_id)
    assert run is not None
    assert run.status == "partial"
    assert run.error


@respx.mock
async def test_wall_clock_budget_yields_partial_run(
    conn: sqlite3.Connection, client: httpx.AsyncClient
) -> None:
    no_robots_no_sitemap()

    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.2)
        return httpx.Response(200, html=html("/a"))

    respx.get("https://e.com/").mock(side_effect=slow)
    respx.get("https://e.com/a").mock(side_effect=slow)
    report = await make(conn, client).run("https://e.com/", wall_clock_seconds=0.1)
    assert report.status == "partial"


@respx.mock
async def test_dry_run_fetches_no_pages(
    conn: sqlite3.Connection, client: httpx.AsyncClient
) -> None:
    respx.get("https://e.com/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://e.com/sitemap.xml").mock(
        return_value=httpx.Response(
            200,
            text='<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            "<url><loc>https://e.com/a</loc></url></urlset>",
        )
    )
    home = respx.get("https://e.com/").mock(return_value=httpx.Response(200, html=html()))
    planned = await make(conn, client).plan("https://e.com/")
    assert planned == ["https://e.com/", "https://e.com/a"]
    assert not home.called
    assert repo_runs.list_runs(conn) == []


@respx.mock
async def test_sitemap_seeds_are_crawled_and_remembered(
    conn: sqlite3.Connection, client: httpx.AsyncClient
) -> None:
    respx.get("https://e.com/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://e.com/sitemap.xml").mock(
        return_value=httpx.Response(
            200,
            text='<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            "<url><loc>https://e.com/orphan</loc></url></urlset>",
        )
    )
    respx.get("https://e.com/").mock(return_value=httpx.Response(200, html=html()))
    respx.get("https://e.com/orphan").mock(return_value=httpx.Response(200, html=html()))
    report = await make(conn, client).run("https://e.com/")
    assert set(urls(conn, report.run_id)) == {"https://e.com/", "https://e.com/orphan"}
    assert repo_pages.sitemap_urls(conn, report.run_id) == {"https://e.com/orphan"}


@respx.mock
async def test_unreachable_robots_fails_the_run_loudly(
    conn: sqlite3.Connection, client: httpx.AsyncClient
) -> None:
    respx.get("https://e.com/robots.txt").mock(side_effect=httpx.ConnectError("tls"))
    respx.get("https://e.com/sitemap.xml").mock(return_value=httpx.Response(404))
    home = respx.get("https://e.com/").mock(return_value=httpx.Response(200, html=html()))
    report = await make(conn, client).run("https://e.com/")
    assert report.status == "failed"
    assert report.pages == 0
    assert report.error is not None
    assert "robots.txt" in report.error
    assert not home.called


@respx.mock
async def test_trailing_slash_redirect_target_is_not_fetched_twice(
    conn: sqlite3.Connection, client: httpx.AsyncClient
) -> None:
    """/x -> /x/ is followed, and a later link to /x (or /x/) is the same page, not a new one."""
    no_robots_no_sitemap()
    respx.get("https://e.com/").mock(return_value=httpx.Response(200, html=html("/x")))
    respx.get("https://e.com/x").mock(return_value=httpx.Response(301, headers={"location": "/x/"}))
    slashed = respx.get("https://e.com/x/").mock(
        return_value=httpx.Response(200, html=html("/x", "/x/"))
    )
    report = await make(conn, client).run("https://e.com/")
    pages = {p.url: p for p in repo_pages.list_pages(conn, report.run_id)}
    assert set(pages) == {"https://e.com/", "https://e.com/x"}
    assert pages["https://e.com/x"].final_url == "https://e.com/x/"
    assert pages["https://e.com/x"].status == 200
    assert slashed.call_count == 1


@respx.mock
async def test_link_written_with_a_slash_is_requested_with_the_slash(
    conn: sqlite3.Connection, client: httpx.AsyncClient
) -> None:
    """The crawler must not manufacture a redirect by requesting the normalised spelling."""
    no_robots_no_sitemap()
    respx.get("https://e.com/").mock(return_value=httpx.Response(200, html=html("/y/")))
    slashed = respx.get("https://e.com/y/").mock(return_value=httpx.Response(200, html=html()))
    report = await make(conn, client).run("https://e.com/")
    pages = {p.url: p for p in repo_pages.list_pages(conn, report.run_id)}
    assert set(pages) == {"https://e.com/", "https://e.com/y"}
    assert slashed.call_count == 1
    assert pages["https://e.com/y"].status == 200
    assert pages["https://e.com/y"].final_url == "https://e.com/y/"
    assert pages["https://e.com/y"].redirect_chain == []


@respx.mock
async def test_start_url_is_requested_as_given(
    conn: sqlite3.Connection, client: httpx.AsyncClient
) -> None:
    respx.get("https://e.com/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://e.com/sitemap.xml").mock(return_value=httpx.Response(404))
    respx.get("https://e.com/docs/sitemap.xml").mock(return_value=httpx.Response(404))
    home = respx.get("https://e.com/docs/").mock(return_value=httpx.Response(200, html=html()))
    report = await make(conn, client).run("https://e.com/docs/")
    pages = {p.url: p for p in repo_pages.list_pages(conn, report.run_id)}
    assert home.call_count == 1
    assert pages["https://e.com/docs"].final_url == "https://e.com/docs/"
    assert pages["https://e.com/docs"].redirect_chain == []


@respx.mock
async def test_robots_is_checked_on_the_spelling_that_gets_requested(
    conn: sqlite3.Connection, client: httpx.AsyncClient
) -> None:
    """`Disallow: /private/` allows /private but not /private/; the check must see the slash."""
    respx.get("https://e.com/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /private/\n")
    )
    respx.get("https://e.com/sitemap.xml").mock(return_value=httpx.Response(404))
    respx.get("https://e.com/").mock(return_value=httpx.Response(200, html=html("/private/")))
    private = respx.get("https://e.com/private/").mock(
        return_value=httpx.Response(200, html=html())
    )
    report = await make(conn, client).run("https://e.com/")
    assert not private.called
    assert set(urls(conn, report.run_id)) == {"https://e.com/"}


@respx.mock
async def test_dry_run_and_crawl_agree_on_a_disallowed_seed(
    conn: sqlite3.Connection, client: httpx.AsyncClient
) -> None:
    respx.get("https://e.com/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /private/\n")
    )
    respx.get("https://e.com/sitemap.xml").mock(return_value=httpx.Response(404))
    respx.get("https://e.com/private/sitemap.xml").mock(return_value=httpx.Response(404))
    seed = respx.get("https://e.com/private/").mock(return_value=httpx.Response(200, html=html()))
    crawler = make(conn, client)
    assert await crawler.plan("https://e.com/private/") == []
    report = await crawler.run("https://e.com/private/")
    assert not seed.called
    assert report.pages == 0


@respx.mock
async def test_redirect_into_a_disallowed_path_is_not_followed(
    conn: sqlite3.Connection, client: httpx.AsyncClient
) -> None:
    """robots.txt applies to every request a page causes, not only the first."""
    respx.get("https://e.com/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /private/\n")
    )
    respx.get("https://e.com/sitemap.xml").mock(return_value=httpx.Response(404))
    respx.get("https://e.com/").mock(return_value=httpx.Response(200, html=html("/go")))
    respx.get("https://e.com/go").mock(
        return_value=httpx.Response(301, headers={"location": "/private/"})
    )
    private = respx.get("https://e.com/private/").mock(
        return_value=httpx.Response(200, html=html("/private/secret"))
    )
    report = await make(conn, client).run("https://e.com/")
    assert not private.called
    assert report.status == "complete"
    pages = {p.url: p for p in repo_pages.list_pages(conn, report.run_id)}
    assert set(pages) == {"https://e.com/", "https://e.com/go"}
    assert pages["https://e.com/go"].status == 301
    assert pages["https://e.com/go"].error == "disallowed_redirect"


@respx.mock
async def test_failed_fetch_stores_the_spelling_that_was_requested(
    conn: sqlite3.Connection, client: httpx.AsyncClient
) -> None:
    no_robots_no_sitemap()
    respx.get("https://e.com/").mock(return_value=httpx.Response(200, html=html("/a/")))
    respx.get("https://e.com/a/").mock(side_effect=httpx.ConnectError("down"))
    report = await make(conn, client).run("https://e.com/")
    pages = {p.url: p for p in repo_pages.list_pages(conn, report.run_id)}
    assert pages["https://e.com/a"].status == 0
    assert pages["https://e.com/a"].final_url == "https://e.com/a/"


@respx.mock
async def test_unfollowable_location_does_not_abort_the_run(
    conn: sqlite3.Connection, client: httpx.AsyncClient
) -> None:
    no_robots_no_sitemap()
    respx.get("https://e.com/").mock(return_value=httpx.Response(200, html=html("/j", "/k")))
    respx.get("https://e.com/j").mock(
        return_value=httpx.Response(301, headers={"location": "javascript:void(0)"})
    )
    respx.get("https://e.com/k").mock(return_value=httpx.Response(200, html=html()))
    report = await make(conn, client).run("https://e.com/")
    assert report.status == "complete"
    pages = {p.url: p for p in repo_pages.list_pages(conn, report.run_id)}
    assert set(pages) == {"https://e.com/", "https://e.com/j", "https://e.com/k"}
    assert pages["https://e.com/j"].error == "bad_redirect"


@respx.mock
async def test_malformed_anchor_does_not_abort_the_run(
    conn: sqlite3.Connection, client: httpx.AsyncClient
) -> None:
    no_robots_no_sitemap()
    respx.get("https://e.com/").mock(
        return_value=httpx.Response(200, html=html("https://[::1/x", "/ok"))
    )
    respx.get("https://e.com/ok").mock(return_value=httpx.Response(200, html=html()))
    report = await make(conn, client).run("https://e.com/")
    assert report.status == "complete"
    assert set(urls(conn, report.run_id)) == {"https://e.com/", "https://e.com/ok"}


@respx.mock
async def test_an_unexpected_error_still_finishes_the_run(
    conn: sqlite3.Connection, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run row must never stay `running`: later passes and diffs would build on it."""
    respx.get("https://e.com/robots.txt").mock(return_value=httpx.Response(404))

    async def boom(*_: Any, **__: Any) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(crawler_module, "discover_seeds", boom)
    with pytest.raises(RuntimeError):
        await make(conn, client).run("https://e.com/")
    (run,) = repo_runs.list_runs(conn)
    assert run.status == "failed"
    assert run.error == "RuntimeError: boom"
    assert run.finished_at is not None


@respx.mock
async def test_a_start_url_httpx_cannot_request_fails_cleanly(
    conn: sqlite3.Connection, client: httpx.AsyncClient
) -> None:
    report = await make(conn, client).run("https://1.2.3.999/")
    assert report.status == "failed"
    assert not respx.calls
    assert repo_runs.get_run(conn, report.run_id).status == "failed"  # type: ignore[union-attr]


@respx.mock
async def test_malformed_sitemap_entries_do_not_abort_the_run(
    conn: sqlite3.Connection, client: httpx.AsyncClient
) -> None:
    respx.get("https://e.com/robots.txt").mock(
        return_value=httpx.Response(
            200, text="Sitemap: https://e.com:abc/sm.xml\nSitemap: https://e.com/sitemap.xml\n"
        )
    )
    index = """<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
    <sitemap><loc>https://[::1/x</loc></sitemap>
    <sitemap><loc>https://e.com:abc/c.xml</loc></sitemap>
    <sitemap><loc>https://e.com/c.xml</loc></sitemap></sitemapindex>"""
    respx.get("https://e.com/sitemap.xml").mock(return_value=httpx.Response(200, text=index))
    respx.get("https://e.com/c.xml").mock(
        return_value=httpx.Response(
            200,
            text='<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            "<url><loc>https://e.com/from-sitemap</loc></url></urlset>",
        )
    )
    respx.get("https://e.com/").mock(return_value=httpx.Response(200, html=html()))
    respx.get("https://e.com/from-sitemap").mock(return_value=httpx.Response(200, html=html()))
    report = await make(conn, client).run("https://e.com/")
    assert report.status == "complete"
    assert set(urls(conn, report.run_id)) == {"https://e.com/", "https://e.com/from-sitemap"}


@respx.mock
async def test_a_link_httpx_cannot_request_is_recorded_not_fatal(
    conn: sqlite3.Connection, client: httpx.AsyncClient
) -> None:
    no_robots_no_sitemap()
    respx.get("https://e.com/").mock(
        return_value=httpx.Response(200, html=html("https://xn--a.e.com/x", "/ok"))
    )
    respx.get("https://e.com/ok").mock(return_value=httpx.Response(200, html=html()))
    report = await make(conn, client).run("https://e.com/")
    assert report.status == "complete"
    pages = {p.url: p for p in repo_pages.list_pages(conn, report.run_id)}
    assert pages["https://xn--a.e.com/x"].error == "bad_url"
    assert pages["https://xn--a.e.com/x"].status == 0
