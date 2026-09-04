"""Pure run-to-run comparison."""

from seo_scout.diff.differ import PageSnapshot, RunSnapshot, diff_runs


def snap(run_id: int, pages: dict[str, tuple[int, set[str]]]) -> RunSnapshot:
    return RunSnapshot(
        run_id=run_id,
        pages={
            url: PageSnapshot(score=score, rules=rules) for url, (score, rules) in pages.items()
        },
    )


def test_identical_runs_produce_an_empty_diff() -> None:
    a = snap(1, {"https://e.com/": (85, {"broken_links"})})
    d = diff_runs(a, snap(2, {"https://e.com/": (85, {"broken_links"})}))
    assert d.pages_added == [] and d.pages_removed == []
    assert d.score_changes == [] and d.issues_fixed == [] and d.issues_introduced == []
    assert d.average_before == d.average_after == 85
    assert d.is_empty


def test_pages_added_and_removed() -> None:
    a = snap(1, {"https://e.com/old": (50, set()), "https://e.com/": (90, set())})
    b = snap(2, {"https://e.com/new": (70, set()), "https://e.com/": (90, set())})
    d = diff_runs(a, b)
    assert d.pages_added == ["https://e.com/new"]
    assert d.pages_removed == ["https://e.com/old"]
    assert not d.is_empty


def test_score_changes_are_sorted_worst_first_with_signed_delta() -> None:
    a = snap(
        1,
        {
            "https://e.com/a": (80, set()),
            "https://e.com/b": (60, set()),
            "https://e.com/c": (70, set()),
        },
    )
    b = snap(
        2,
        {
            "https://e.com/a": (95, set()),
            "https://e.com/b": (40, set()),
            "https://e.com/c": (70, set()),
        },
    )
    d = diff_runs(a, b)
    assert [(c.url, c.before, c.after, c.delta) for c in d.score_changes] == [
        ("https://e.com/b", 60, 40, -20),
        ("https://e.com/a", 80, 95, 15),
    ]


def test_issues_fixed_and_introduced_per_page() -> None:
    a = snap(1, {"https://e.com/": (70, {"title_missing", "h1_missing"})})
    b = snap(2, {"https://e.com/": (80, {"h1_missing", "og_missing"})})
    d = diff_runs(a, b)
    assert [(i.url, i.rule_id) for i in d.issues_fixed] == [("https://e.com/", "title_missing")]
    assert [(i.url, i.rule_id) for i in d.issues_introduced] == [("https://e.com/", "og_missing")]


def test_added_and_removed_pages_do_not_count_as_fixed_or_introduced() -> None:
    a = snap(1, {"https://e.com/gone": (0, {"title_missing"})})
    b = snap(2, {"https://e.com/new": (0, {"title_missing"})})
    d = diff_runs(a, b)
    assert d.issues_fixed == [] and d.issues_introduced == []


def test_averages_handle_empty_runs() -> None:
    d = diff_runs(snap(1, {}), snap(2, {"https://e.com/": (50, set())}))
    assert d.average_before == 0 and d.average_after == 50
    assert d.pages_added == ["https://e.com/"]
