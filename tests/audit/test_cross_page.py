"""Rules that need site-wide context: duplicates, orphans, broken links."""

from seo_scout.audit.engine import audit_page, audit_pages, build_context
from seo_scout.audit.page import AuditPage

TITLE = "Fresh Roasted Coffee Beans Delivered Weekly"
META = (
    "Order freshly roasted single-origin coffee beans, delivered to your door every "
    "week with free tasting notes and brewing guides."
)


def page(
    url: str, *, title: str = TITLE, meta: str = META, links: tuple[str, ...] = ()
) -> AuditPage:
    anchors = "".join(f'<a href="{link}">x</a>' for link in links)
    words = " ".join(f"word{i}" for i in range(320))
    html = (
        f'<html lang="en"><head><title>{title}</title>'
        f'<meta name="description" content="{meta}">'
        f'<link rel="canonical" href="{url}">'
        '<meta property="og:title" content="t"><meta property="og:description" content="d">'
        f"</head><body><h1>H</h1><p>{words}</p>{anchors}</body></html>"
    )
    return AuditPage.build(url=url, final_url=url, html=html)


def test_duplicate_titles_and_metas_flag_every_page_involved() -> None:
    pages = [
        page("https://e.com/a"),
        page("https://e.com/b"),
        page("https://e.com/c", title="Unique Title For Page C Only Here"),
    ]
    ctx = build_context(pages, inbound={}, sitemap_urls=set(), status_by_url={})
    results = audit_pages(pages, ctx)
    assert {i.rule_id for i in results["https://e.com/a"]} == {
        "title_duplicate",
        "meta_description_duplicate",
    }
    assert {i.rule_id for i in results["https://e.com/b"]} == {
        "title_duplicate",
        "meta_description_duplicate",
    }
    assert {i.rule_id for i in results["https://e.com/c"]} == {"meta_description_duplicate"}
    title_issue = next(i for i in results["https://e.com/a"] if i.rule_id == "title_duplicate")
    assert "2 pages" in title_issue.message
    meta_issue = next(
        i for i in results["https://e.com/c"] if i.rule_id == "meta_description_duplicate"
    )
    assert "3 pages" in meta_issue.message


def test_broken_internal_links_report_targets() -> None:
    home = page("https://e.com/", links=("/dead", "/gone", "/ok", "https://other.com/x"))
    statuses = {"https://e.com/dead": 404, "https://e.com/gone": 500, "https://e.com/ok": 200}
    ctx = build_context([home], inbound={}, sitemap_urls=set(), status_by_url=statuses)
    (issue,) = [i for i in audit_page(home, ctx) if i.rule_id == "broken_links"]
    assert "2 broken" in issue.message
    assert "https://e.com/dead" in issue.message


def test_broken_link_written_with_a_trailing_slash_is_still_reported() -> None:
    """Statuses are keyed by page identity; a link's spelling must not hide a 404."""
    home = page("https://e.com/", links=("/dead/", "/ok/"))
    statuses = {"https://e.com/dead": 404, "https://e.com/ok": 200}
    ctx = build_context([home], inbound={}, sitemap_urls=set(), status_by_url=statuses)
    (issue,) = [i for i in audit_page(home, ctx) if i.rule_id == "broken_links"]
    assert "1 broken" in issue.message
    assert "https://e.com/dead" in issue.message


def test_orphan_pages_are_in_sitemap_with_no_inbound_links() -> None:
    home = page("https://e.com/", links=("/linked",))
    linked = page("https://e.com/linked")
    orphan = page("https://e.com/orphan")
    ctx = build_context(
        [home, linked, orphan],
        inbound={"https://e.com/linked": 1},
        sitemap_urls={"https://e.com/orphan", "https://e.com/linked", "https://e.com/"},
        status_by_url={},
    )
    results = audit_pages([home, linked, orphan], ctx)
    assert "orphan_page" in {i.rule_id for i in results["https://e.com/orphan"]}
    assert "orphan_page" not in {i.rule_id for i in results["https://e.com/linked"]}
    assert "orphan_page" not in {
        i.rule_id for i in results["https://e.com/"]
    }  # homepage is never an orphan


def test_unlinked_page_not_in_sitemap_is_not_an_orphan() -> None:
    stray = page("https://e.com/stray")
    ctx = build_context([stray], inbound={}, sitemap_urls=set(), status_by_url={})
    assert "orphan_page" not in {i.rule_id for i in audit_page(stray, ctx)}


def test_one_broken_link_is_reported_in_the_singular() -> None:
    """The message is the report a client reads; "1 broken internal links" is not English."""
    home = page("https://e.com/", links=("/dead", "/ok"))
    statuses = {"https://e.com/dead": 404, "https://e.com/ok": 200}
    ctx = build_context([home], inbound={}, sitemap_urls=set(), status_by_url=statuses)
    (issue,) = [i for i in audit_page(home, ctx) if i.rule_id == "broken_links"]
    assert "1 broken internal link:" in issue.message


def test_several_broken_links_stay_plural() -> None:
    home = page("https://e.com/", links=("/dead", "/gone"))
    statuses = {"https://e.com/dead": 404, "https://e.com/gone": 500}
    ctx = build_context([home], inbound={}, sitemap_urls=set(), status_by_url=statuses)
    (issue,) = [i for i in audit_page(home, ctx) if i.rule_id == "broken_links"]
    assert "2 broken internal links:" in issue.message
