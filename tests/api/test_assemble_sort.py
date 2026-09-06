"""The API's score order must agree with `summarize_run`'s worst-page ranking on ties."""

from seo_scout.api.assemble import select
from seo_scout.api.schemas import PageView
from seo_scout.models import Issue, Severity


def _page(url: str, score: int, issues: int) -> PageView:
    found = [Issue(rule_id="r", severity=Severity.warning, message="m") for _ in range(issues)]
    return PageView(
        url=url,
        final_url=url,
        status=200,
        depth=0,
        bytes=0,
        elapsed_ms=0,
        score=score,
        counts={"critical": 0, "warning": issues, "notice": 0},
        issues=found,
        ai=None,
    )


def test_score_ties_break_on_issue_count_then_url() -> None:
    pages = [
        _page("https://e.com/c", 0, 1),
        _page("https://e.com/b", 0, 3),
        _page("https://e.com/a", 0, 1),
    ]
    ordered = select(pages, severity=None, rule=None, sort="score", order="asc")
    assert [p.url for p in ordered] == ["https://e.com/b", "https://e.com/a", "https://e.com/c"]
