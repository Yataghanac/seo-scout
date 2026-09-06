import httpx
import pytest
import respx

from seo_scout.crawler.fetch import Fetcher, NetworkError

UA = "SEOScout/0.1 (+https://github.com/Yataghanac/seo-scout)"


class Sleeps:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def make(client: httpx.AsyncClient, sleeps: Sleeps) -> Fetcher:
    return Fetcher(client, user_agent=UA, max_bytes=1000, timeout=5.0, sleep=sleeps)


@respx.mock
async def test_html_page(client: httpx.AsyncClient) -> None:
    route = respx.get("https://e.com/").mock(
        return_value=httpx.Response(
            200, text="<title>x</title>", headers={"content-type": "text/html; charset=utf-8"}
        )
    )
    r = await make(client, Sleeps()).fetch("https://e.com/")
    assert r.status == 200
    assert r.body == "<title>x</title>"
    assert r.content_type == "text/html"
    assert r.final_url == "https://e.com/"
    assert r.redirect_chain == []
    assert r.bytes == len("<title>x</title>")
    assert r.elapsed_ms >= 0
    assert route.calls.last.request.headers["user-agent"] == UA


@respx.mock
async def test_redirect_chain_is_recorded(client: httpx.AsyncClient) -> None:
    respx.get("https://e.com/a").mock(return_value=httpx.Response(301, headers={"location": "/b"}))
    respx.get("https://e.com/b").mock(
        return_value=httpx.Response(302, headers={"location": "https://e.com/c"})
    )
    respx.get("https://e.com/c").mock(return_value=httpx.Response(200, html="<p>c</p>"))
    r = await make(client, Sleeps()).fetch("https://e.com/a")
    assert r.status == 200
    assert r.final_url == "https://e.com/c"
    assert [(h.url, h.status) for h in r.redirect_chain] == [
        ("https://e.com/a", 301),
        ("https://e.com/b", 302),
    ]


@respx.mock
async def test_redirect_loop_gives_up_cleanly(client: httpx.AsyncClient) -> None:
    respx.get("https://e.com/a").mock(return_value=httpx.Response(301, headers={"location": "/b"}))
    respx.get("https://e.com/b").mock(return_value=httpx.Response(301, headers={"location": "/a"}))
    r = await make(client, Sleeps()).fetch("https://e.com/a")
    assert r.skipped == "redirect_loop"
    assert r.body is None
    assert r.status == 301


@respx.mock
async def test_off_site_redirect_is_not_followed(client: httpx.AsyncClient) -> None:
    respx.get("https://e.com/a").mock(
        return_value=httpx.Response(302, headers={"location": "https://other.com/"})
    )
    other = respx.get("https://other.com/").mock(return_value=httpx.Response(200, html="x"))
    r = await make(client, Sleeps()).fetch("https://e.com/a")
    assert r.skipped == "off_site_redirect"
    assert r.final_url == "https://other.com/"
    assert not other.called


@respx.mock
async def test_429_backs_off_honouring_retry_after(client: httpx.AsyncClient) -> None:
    respx.get("https://e.com/").mock(
        side_effect=[
            httpx.Response(429, headers={"retry-after": "3"}),
            httpx.Response(200, html="ok"),
        ]
    )
    sleeps = Sleeps()
    r = await make(client, sleeps).fetch("https://e.com/")
    assert r.status == 200
    assert sleeps.calls == [3.0]


@respx.mock
async def test_5xx_gives_up_after_three_tries_with_backoff(client: httpx.AsyncClient) -> None:
    route = respx.get("https://e.com/").mock(return_value=httpx.Response(503))
    sleeps = Sleeps()
    r = await make(client, sleeps).fetch("https://e.com/")
    assert r.status == 503
    assert r.body is None
    assert route.call_count == 3
    assert sleeps.calls == [0.5, 1.0]


@respx.mock
async def test_4xx_is_recorded_not_retried(client: httpx.AsyncClient) -> None:
    route = respx.get("https://e.com/missing").mock(return_value=httpx.Response(404, html="gone"))
    r = await make(client, Sleeps()).fetch("https://e.com/missing")
    assert r.status == 404
    assert route.call_count == 1


@respx.mock
async def test_non_html_content_type_is_skipped(client: httpx.AsyncClient) -> None:
    respx.get("https://e.com/data").mock(
        return_value=httpx.Response(
            200, content=b"%PDF", headers={"content-type": "application/pdf"}
        )
    )
    r = await make(client, Sleeps()).fetch("https://e.com/data")
    assert r.skipped == "non_html"
    assert r.body is None
    assert r.content_type == "application/pdf"


@respx.mock
async def test_binary_extension_uses_head_first(client: httpx.AsyncClient) -> None:
    head = respx.head("https://e.com/file.pdf").mock(
        return_value=httpx.Response(200, headers={"content-type": "application/pdf"})
    )
    get = respx.get("https://e.com/file.pdf").mock(return_value=httpx.Response(200))
    r = await make(client, Sleeps()).fetch("https://e.com/file.pdf")
    assert r.skipped == "non_html"
    assert head.called
    assert not get.called


@respx.mock
async def test_head_saying_html_falls_through_to_get(client: httpx.AsyncClient) -> None:
    respx.head("https://e.com/odd.pdf").mock(
        return_value=httpx.Response(200, headers={"content-type": "text/html"})
    )
    respx.get("https://e.com/odd.pdf").mock(return_value=httpx.Response(200, html="<p>x</p>"))
    r = await make(client, Sleeps()).fetch("https://e.com/odd.pdf")
    assert r.body == "<p>x</p>"


@respx.mock
async def test_content_length_over_cap_is_skipped(client: httpx.AsyncClient) -> None:
    respx.get("https://e.com/big").mock(
        return_value=httpx.Response(200, html="x", headers={"content-length": "999999"})
    )
    r = await make(client, Sleeps()).fetch("https://e.com/big")
    assert r.skipped == "too_large"


@respx.mock
async def test_streamed_body_over_cap_is_skipped(client: httpx.AsyncClient) -> None:
    respx.get("https://e.com/big").mock(return_value=httpx.Response(200, html="y" * 5000))
    r = await make(client, Sleeps()).fetch("https://e.com/big")
    assert r.skipped == "too_large"
    assert r.body is None


@respx.mock
async def test_connection_errors_retry_then_raise(client: httpx.AsyncClient) -> None:
    route = respx.get("https://e.com/").mock(side_effect=httpx.ConnectError("down"))
    sleeps = Sleeps()
    with pytest.raises(NetworkError):
        await make(client, sleeps).fetch("https://e.com/")
    assert route.call_count == 3
    assert len(sleeps.calls) == 2


@respx.mock
async def test_timeout_counts_as_network_error(client: httpx.AsyncClient) -> None:
    respx.get("https://e.com/").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(NetworkError):
        await make(client, Sleeps()).fetch("https://e.com/")


@respx.mock
async def test_redirect_to_trailing_slash_variant_is_followed(client: httpx.AsyncClient) -> None:
    """A 301 from /a to /a/ must be requested as sent, not re-normalised back to /a."""
    respx.get("https://e.com/a").mock(return_value=httpx.Response(301, headers={"location": "/a/"}))
    slashed = respx.get("https://e.com/a/").mock(return_value=httpx.Response(200, html="<p>a</p>"))
    r = await make(client, Sleeps()).fetch("https://e.com/a")
    assert slashed.called
    assert r.status == 200
    assert r.skipped is None
    assert r.final_url == "https://e.com/a/"
    assert [(h.url, h.status) for h in r.redirect_chain] == [("https://e.com/a", 301)]


@respx.mock
async def test_fragment_only_location_is_a_loop_after_one_request(
    client: httpx.AsyncClient,
) -> None:
    route = respx.get("https://e.com/a").mock(
        return_value=httpx.Response(301, headers={"location": "#top"})
    )
    r = await make(client, Sleeps()).fetch("https://e.com/a")
    assert route.call_count == 1
    assert r.skipped == "redirect_loop"
    assert r.final_url == "https://e.com/a"


@respx.mock
async def test_fragment_in_location_is_dropped(client: httpx.AsyncClient) -> None:
    respx.get("https://e.com/a").mock(
        return_value=httpx.Response(301, headers={"location": "/b#x"})
    )
    respx.get("https://e.com/b").mock(return_value=httpx.Response(200, html="<p>b</p>"))
    r = await make(client, Sleeps()).fetch("https://e.com/a")
    assert r.status == 200
    assert r.final_url == "https://e.com/b"


@respx.mock
async def test_two_hop_redirect_cycle_stops_after_one_lap(client: httpx.AsyncClient) -> None:
    a = respx.get("https://e.com/a").mock(
        return_value=httpx.Response(301, headers={"location": "/b"})
    )
    b = respx.get("https://e.com/b").mock(
        return_value=httpx.Response(301, headers={"location": "/a"})
    )
    r = await make(client, Sleeps()).fetch("https://e.com/a")
    assert r.skipped == "redirect_loop"
    assert (a.call_count, b.call_count) == (1, 1)
    assert [h.url for h in r.redirect_chain] == ["https://e.com/a", "https://e.com/b"]


@respx.mock
@pytest.mark.parametrize(
    "location", ["HTTPS://E.COM/a", "https://e.com:443/a", "https://e.com/a#x"]
)
async def test_location_differing_only_on_the_wire_is_a_self_redirect(
    client: httpx.AsyncClient, location: str
) -> None:
    """Case, a default port or a fragment never change what is requested: one hop, not ten."""
    route = respx.get("https://e.com/a").mock(
        return_value=httpx.Response(301, headers={"location": location})
    )
    r = await make(client, Sleeps()).fetch("https://e.com/a")
    assert route.call_count == 1
    assert r.skipped == "redirect_loop"


@respx.mock
async def test_path_spelling_is_still_a_real_hop(client: httpx.AsyncClient) -> None:
    """/a and /a/ are different requests, so the loop check must not collapse them."""
    respx.get("https://e.com/a").mock(return_value=httpx.Response(301, headers={"location": "/a/"}))
    respx.get("https://e.com/a/").mock(return_value=httpx.Response(200, html="<p>a</p>"))
    r = await make(client, Sleeps()).fetch("https://e.com/a")
    assert r.status == 200
    assert r.final_url == "https://e.com/a/"


@respx.mock
async def test_a_long_chain_of_distinct_hops_is_too_many_redirects(
    client: httpx.AsyncClient,
) -> None:
    for i in range(12):
        respx.get(f"https://e.com/h{i}").mock(
            return_value=httpx.Response(301, headers={"location": f"/h{i + 1}"})
        )
    r = await make(client, Sleeps()).fetch("https://e.com/h0")
    assert r.skipped == "too_many_redirects"
    assert len(r.redirect_chain) == 10


@respx.mock
@pytest.mark.parametrize("location", ["javascript:void(0)", "mailto:x@y.z", "ftp://e.com/x"])
async def test_unfollowable_location_is_a_bad_redirect_not_a_crash(
    client: httpx.AsyncClient, location: str
) -> None:
    """httpx builds the next request even with follow_redirects=False and can raise on it."""
    route = respx.get("https://e.com/a").mock(
        return_value=httpx.Response(301, headers={"location": location})
    )
    r = await make(client, Sleeps()).fetch("https://e.com/a")
    assert route.call_count == 1
    assert r.skipped == "bad_redirect"
    assert r.body is None
    assert r.final_url == "https://e.com/a"


@respx.mock
async def test_redirect_into_a_disallowed_path_is_not_requested(client: httpx.AsyncClient) -> None:
    respx.get("https://e.com/go").mock(
        return_value=httpx.Response(301, headers={"location": "/private/"})
    )
    private = respx.get("https://e.com/private/").mock(return_value=httpx.Response(200, html="s"))
    fetcher = make(client, Sleeps())
    r = await fetcher.fetch("https://e.com/go", allowed=lambda u: "/private/" not in u)
    assert not private.called
    assert r.skipped == "disallowed_redirect"
    assert r.status == 301
    assert r.final_url == "https://e.com/private/"
    assert [h.url for h in r.redirect_chain] == ["https://e.com/go"]
