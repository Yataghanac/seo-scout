"""Table-driven rule tests: each fixture asserts exactly which rule ids fire."""

from pathlib import Path

import pytest

from seo_scout.audit.engine import audit_page, build_context
from seo_scout.audit.page import AuditPage
from seo_scout.audit.registry import RULES
from seo_scout.models import Severity

FIXTURES = Path(__file__).parent / "fixtures"

CASES: dict[str, set[str]] = {
    "clean": set(),
    "title_missing": {"title_missing"},
    "title_short": {"title_too_short"},
    "title_long": {"title_too_long"},
    "meta_missing": {"meta_description_missing"},
    "meta_short": {"meta_description_too_short"},
    "meta_long": {"meta_description_too_long"},
    "h1_missing": {"h1_missing"},
    "h1_multiple": {"h1_multiple"},
    "thin_content": {"thin_content"},
    "canonical_missing": {"canonical_missing"},
    "canonical_conflict": {"canonical_conflict"},
    "canonical_off_domain": {"canonical_off_domain"},
    "noindex_meta": {"noindex", "nofollow"},
    "images_no_alt": {"images_missing_alt"},
    "lang_missing": {"lang_missing"},
    "og_missing": {"og_missing"},
    "kitchen_sink": {
        "title_missing",
        "meta_description_missing",
        "h1_missing",
        "thin_content",
        "canonical_missing",
        "lang_missing",
        "og_missing",
        "images_missing_alt",
    },
    "empty": {
        "title_missing",
        "meta_description_missing",
        "h1_missing",
        "thin_content",
        "canonical_missing",
        "lang_missing",
        "og_missing",
    },
}


def load(name: str, **overrides: object) -> AuditPage:
    html = (FIXTURES / f"{name}.html").read_text(encoding="utf-8")
    url = f"https://e.com/{name}"
    return AuditPage.build(url=url, final_url=url, html=html, **overrides)  # type: ignore[arg-type]


@pytest.mark.parametrize(("name", "expected"), sorted(CASES.items()))
def test_fixture_fires_exactly_these_rules(name: str, expected: set[str]) -> None:
    page = load(name)
    ctx = build_context([page], inbound={}, sitemap_urls=set(), status_by_url={})
    fired = {issue.rule_id for issue in audit_page(page, ctx)}
    assert fired == expected


def test_every_fixture_case_exists_on_disk() -> None:
    assert {p.stem for p in FIXTURES.glob("*.html")} == set(CASES)


def test_every_rule_id_is_covered_by_a_case_or_a_dedicated_test() -> None:
    covered = set().union(*CASES.values())
    dedicated = {
        "title_duplicate",
        "meta_description_duplicate",
        "broken_links",
        "redirect_chain",
        "orphan_page",
        "page_too_heavy",
        "slow_response",
        "x_robots_tag",
    }
    assert {r.id for r in RULES} == covered | dedicated


def test_every_rule_has_an_explanation_and_severity() -> None:
    for rule in RULES:
        assert rule.explanation.strip(), rule.id
        assert isinstance(rule.severity, Severity), rule.id
        assert rule.id == rule.id.lower() and " " not in rule.id


def test_messages_mention_the_offending_value() -> None:
    page = load("title_short")
    ctx = build_context([page], inbound={}, sitemap_urls=set(), status_by_url={})
    (issue,) = audit_page(page, ctx)
    assert "Short title" in issue.message
    assert "11" in issue.message


def test_header_rules_fire_from_fetched_metadata() -> None:
    page = load(
        "clean",
        headers={"x-robots-tag": "noindex"},
        bytes=3 * 1024 * 1024,
        elapsed_ms=2500,
        redirect_chain=[
            {"url": "https://e.com/a", "status": 301},
            {"url": "https://e.com/b", "status": 302},
        ],
    )
    ctx = build_context([page], inbound={}, sitemap_urls=set(), status_by_url={})
    fired = {i.rule_id for i in audit_page(page, ctx)}
    assert fired == {"x_robots_tag", "page_too_heavy", "slow_response", "redirect_chain"}


def test_single_hop_redirect_is_fine() -> None:
    page = load("clean", redirect_chain=[{"url": "https://e.com/a", "status": 301}])
    ctx = build_context([page], inbound={}, sitemap_urls=set(), status_by_url={})
    assert audit_page(page, ctx) == []


def _with_images(missing: int, total: int) -> AuditPage:
    """A clean page carrying `total` images, the first `missing` of them without an alt."""
    imgs = "".join(
        f'<img src="/{i}.png">' if i < missing else f'<img src="/{i}.png" alt="x">'
        for i in range(total)
    )
    words = " ".join(f"word{i}" for i in range(320))
    html = (
        '<html lang="en"><head><title>A title long enough to satisfy the rule here</title>'
        '<meta name="description" content="A description written to sit inside the seventy '
        'to one hundred and sixty character window the audit asks for.">'
        '<link rel="canonical" href="https://e.com/">'
        '<meta property="og:title" content="t"><meta property="og:description" content="d">'
        f"</head><body><h1>H</h1><p>{words}</p>{imgs}</body></html>"
    )
    return AuditPage.build(url="https://e.com/", final_url="https://e.com/", html=html)


def test_one_image_without_alt_is_reported_in_the_singular() -> None:
    page = _with_images(missing=1, total=2)
    ctx = build_context([page], inbound={}, sitemap_urls=set(), status_by_url={})
    (issue,) = [i for i in audit_page(page, ctx) if i.rule_id == "images_missing_alt"]
    assert issue.message == "1 of 2 images has no alt attribute"


def test_several_images_without_alt_stay_plural() -> None:
    page = _with_images(missing=2, total=3)
    ctx = build_context([page], inbound={}, sitemap_urls=set(), status_by_url={})
    (issue,) = [i for i in audit_page(page, ctx) if i.rule_id == "images_missing_alt"]
    assert issue.message == "2 of 3 images have no alt attribute"
