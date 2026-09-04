"""Page score: 100 minus the weight of every issue, floored at zero."""

from __future__ import annotations

from collections.abc import Iterable

from seo_scout.models import Issue


def score_for(issues: Iterable[Issue]) -> int:
    penalty = sum(issue.severity.weight for issue in issues)
    return max(0, 100 - penalty)


def bucket(score: int) -> str:
    """Histogram bucket label used by the site-level rollup."""
    if score >= 80:
        return "80-100"
    low = (score // 20) * 20
    return f"{low}-{low + 19}"
