from seo_scout.crawler.frontier import Frontier


def test_bfs_order_and_dedupe() -> None:
    f = Frontier(max_depth=5)
    assert f.add("https://e.com/a", 0)
    assert f.add("https://e.com/b", 0)
    assert not f.add("https://e.com/a", 1)
    assert f.pop() == ("https://e.com/a", 0, "https://e.com/a")
    assert f.pop() == ("https://e.com/b", 0, "https://e.com/b")
    assert f.pop() is None
    assert len(f) == 0


def test_depth_cap() -> None:
    f = Frontier(max_depth=1)
    assert f.add("https://e.com/ok", 1)
    assert not f.add("https://e.com/deep", 2)
    assert f.seen == {"https://e.com/ok"}


def test_request_url_travels_with_the_identity_key() -> None:
    """Dedupe on the normalised key; fetch the spelling the link actually used."""
    f = Frontier(max_depth=5)
    assert f.add("https://e.com/x", 0, request="https://e.com/x/")
    assert not f.add("https://e.com/x", 1, request="https://e.com/x")
    assert f.pop() == ("https://e.com/x", 0, "https://e.com/x/")
