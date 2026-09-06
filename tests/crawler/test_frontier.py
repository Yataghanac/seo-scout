from seo_scout.crawler.frontier import Frontier
from seo_scout.urls import Link


def test_bfs_order_and_dedupe() -> None:
    f = Frontier(max_depth=5)
    a, b = Link("https://e.com/a", "https://e.com/a"), Link("https://e.com/b", "https://e.com/b")
    assert f.add(a, 0)
    assert f.add(b, 0)
    assert not f.add(a, 1)
    assert f.pop() == (a, 0)
    assert f.pop() == (b, 0)
    assert f.pop() is None
    assert len(f) == 0


def test_depth_cap() -> None:
    f = Frontier(max_depth=1)
    assert f.add(Link("https://e.com/ok", "https://e.com/ok/"), 1)
    assert not f.add(Link("https://e.com/deep", "https://e.com/deep/"), 2)
    assert f.seen == {"https://e.com/ok"}


def test_request_url_travels_with_the_identity_key() -> None:
    """Dedupe on the normalised key; fetch the spelling the link actually used."""
    f = Frontier(max_depth=5)
    first = Link("https://e.com/x", "https://e.com/x/")
    assert f.add(first, 0)
    assert not f.add(Link("https://e.com/x", "https://e.com/x"), 1)
    assert f.pop() == (first, 0)
