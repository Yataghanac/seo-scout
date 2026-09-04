from seo_scout.audit.score import score_for
from seo_scout.models import Issue, Severity


def issue(severity: Severity) -> Issue:
    return Issue(rule_id="x", severity=severity, message="m")


def test_clean_page_scores_100() -> None:
    assert score_for([]) == 100


def test_weights_subtract_per_issue() -> None:
    issues = [issue(Severity.critical), issue(Severity.warning), issue(Severity.notice)]
    assert score_for(issues) == 100 - 15 - 5 - 2


def test_score_never_goes_below_zero() -> None:
    assert score_for([issue(Severity.critical)] * 10) == 0
