"""The CSV export is read by spreadsheets, which is a second untrusted-data sink."""

from __future__ import annotations

import csv
import io

import pytest

from seo_scout.api.export import csv_rows
from seo_scout.api.schemas import PageView, SuggestionRow


def _page(**ai_fields: object) -> PageView:
    defaults: dict[str, object] = {
        "url": "https://e.com/",
        "original_title": None,
        "original_meta": None,
        "diagnosis": None,
        "title": None,
        "meta_description": None,
        "status": "ok",
        "reason": None,
        "cached": False,
        "cost_usd": 0.0,
    }
    defaults.update(ai_fields)
    return PageView(
        url="https://e.com/",
        final_url="https://e.com/",
        status=200,
        depth=0,
        bytes=1,
        elapsed_ms=1,
        score=100,
        counts={"critical": 0, "warning": 0, "notice": 0},
        issues=[],
        ai=SuggestionRow(**defaults),  # type: ignore[arg-type]
    )


def _row(page: PageView) -> dict[str, str]:
    text = "".join(csv_rows([page]))
    return next(iter(csv.DictReader(io.StringIO(text))))


@pytest.mark.parametrize("lead", ["=", "+", "-", "@", "\t", "\r"])
def test_a_title_that_opens_with_a_formula_character_is_exported_as_text(lead: str) -> None:
    """The audited site chooses its own <title>; a spreadsheet must not run it."""
    hostile = lead + "HYPERLINK(0)"
    row = _row(_page(original_title=hostile))
    assert row["original_title"] == "'" + hostile


def test_the_neutralised_value_still_carries_the_original_text() -> None:
    row = _row(_page(original_title="=1+1"))
    assert row["original_title"].lstrip("'") == "=1+1"


def test_an_ordinary_title_is_left_exactly_alone() -> None:
    row = _row(_page(original_title="Buy shoes | Example"))
    assert row["original_title"] == "Buy shoes | Example"


def test_every_column_carrying_page_or_model_text_is_covered() -> None:
    row = _row(
        _page(
            original_title="=a",
            original_meta="=b",
            title="=c",
            meta_description="=d",
            reason="=e",
        )
    )
    for column in ("original_title", "original_meta", "ai_title", "ai_meta", "ai_reason"):
        assert row[column].startswith("'="), column
