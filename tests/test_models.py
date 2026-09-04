from seo_scout.models import Issue, Severity


def test_severity_weights_are_ordered() -> None:
    assert Severity.critical.weight > Severity.warning.weight > Severity.notice.weight > 0


def test_issue_is_frozen_and_serialisable() -> None:
    issue = Issue(rule_id="title_missing", severity=Severity.critical, message="No <title>")
    assert issue.model_dump()["severity"] == "critical"
