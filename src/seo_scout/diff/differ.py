"""Pure run-to-run comparison. Snapshots in, a RunDiff out, no I/O."""

from __future__ import annotations

from pydantic import BaseModel


class PageSnapshot(BaseModel):
    score: int
    rules: set[str]


class RunSnapshot(BaseModel):
    run_id: int
    pages: dict[str, PageSnapshot]

    @property
    def average(self) -> float:
        if not self.pages:
            return 0.0
        return round(sum(p.score for p in self.pages.values()) / len(self.pages), 1)


class ScoreChange(BaseModel):
    url: str
    before: int
    after: int
    delta: int


class IssueChange(BaseModel):
    url: str
    rule_id: str


class RunDiff(BaseModel):
    run_a: int
    run_b: int
    pages_before: int
    pages_after: int
    average_before: float
    average_after: float
    pages_added: list[str]
    pages_removed: list[str]
    score_changes: list[ScoreChange]  # worst delta first
    issues_fixed: list[IssueChange]
    issues_introduced: list[IssueChange]

    @property
    def is_empty(self) -> bool:
        return not (
            self.pages_added
            or self.pages_removed
            or self.score_changes
            or self.issues_fixed
            or self.issues_introduced
        )


def diff_runs(a: RunSnapshot, b: RunSnapshot) -> RunDiff:
    """Compare two runs of the same site. Only pages present in both count for issue changes."""
    common = sorted(a.pages.keys() & b.pages.keys())
    changes = [
        ScoreChange(url=url, before=a.pages[url].score, after=b.pages[url].score, delta=delta)
        for url in common
        if (delta := b.pages[url].score - a.pages[url].score) != 0
    ]
    fixed = [
        IssueChange(url=url, rule_id=rule)
        for url in common
        for rule in sorted(a.pages[url].rules - b.pages[url].rules)
    ]
    introduced = [
        IssueChange(url=url, rule_id=rule)
        for url in common
        for rule in sorted(b.pages[url].rules - a.pages[url].rules)
    ]
    return RunDiff(
        run_a=a.run_id,
        run_b=b.run_id,
        pages_before=len(a.pages),
        pages_after=len(b.pages),
        average_before=a.average,
        average_after=b.average,
        pages_added=sorted(b.pages.keys() - a.pages.keys()),
        pages_removed=sorted(a.pages.keys() - b.pages.keys()),
        score_changes=sorted(changes, key=lambda c: (c.delta, c.url)),
        issues_fixed=fixed,
        issues_introduced=introduced,
    )
