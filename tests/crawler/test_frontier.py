from seo_scout.crawler.frontier import Frontier


def test_bfs_order_and_dedupe() -> None:
    f = Frontier(max_depth=5)
    assert f.add("https://e.com/a", 0)
    assert f.add("https://e.com/b", 0)
    assert not f.add("https://e.com/a", 1)
    assert f.pop() == ("https://e.com/a", 0)
    assert f.pop() == ("https://e.com/b", 0)
    assert f.pop() is None
    assert len(f) == 0


def test_depth_cap() -> None:
    f = Frontier(max_depth=1)
    assert f.add("https://e.com/ok", 1)
    assert not f.add("https://e.com/deep", 2)
    assert f.seen == {"https://e.com/ok"}
