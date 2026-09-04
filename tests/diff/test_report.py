import json

from seo_scout.diff.differ import PageSnapshot, RunSnapshot, diff_runs
from seo_scout.diff.report import to_json, to_markdown, to_table


def sample() -> tuple[RunSnapshot, RunSnapshot]:
    a = RunSnapshot(
        run_id=1,
        pages={
            "https://e.com/": PageSnapshot(score=70, rules={"title_missing", "h1_missing"}),
            "https://e.com/old": PageSnapshot(score=50, rules=set()),
        },
    )
    b = RunSnapshot(
        run_id=2,
        pages={
            "https://e.com/": PageSnapshot(score=85, rules={"h1_missing", "og_missing"}),
            "https://e.com/new": PageSnapshot(score=90, rules=set()),
        },
    )
    return a, b


def test_table_lists_every_section() -> None:
    text = to_table(diff_runs(*sample()))
    assert "run 1 -> run 2" in text
    assert "average score" in text and "70" in text and "85" in text
    assert "https://e.com/new" in text and "https://e.com/old" in text
    assert "+15" in text
    assert "title_missing" in text and "og_missing" in text


def test_empty_diff_says_so() -> None:
    a, _ = sample()
    text = to_table(diff_runs(a, RunSnapshot(run_id=3, pages=dict(a.pages))))
    assert "no changes" in text.lower()


def test_markdown_has_headings_and_table_rows() -> None:
    md = to_markdown(diff_runs(*sample()))
    assert md.startswith("# ")
    assert "## Score changes" in md
    assert "| https://e.com/ | 70 | 85 | +15 |" in md
    assert "- https://e.com/new" in md


def test_json_is_loadable_and_complete() -> None:
    body = json.loads(to_json(diff_runs(*sample())))
    assert body["run_a"] == 1 and body["run_b"] == 2
    assert set(body) >= {
        "pages_added",
        "pages_removed",
        "score_changes",
        "issues_fixed",
        "issues_introduced",
    }
    assert body["score_changes"][0]["delta"] == 15
