"""The validator is the most important file in the project. Test it hardest."""

import pytest

from seo_scout.ai.schema import Suggestion
from seo_scout.ai.validate import PageFacts, Violation, summarize_violations, validate

GOOD_TITLE = "Fresh Roasted Coffee Beans Delivered Weekly"  # 43 chars
GOOD_META = (
    "Order freshly roasted single-origin coffee beans, delivered to your door every "
    "week with tasting notes and brewing guides."
)  # 121 chars
BODY = (
    "We roast single-origin coffee beans in small batches and deliver them weekly. "
    "Founded in 2015, we ship to 40 countries. A 250 g bag costs $14.99. "
    "Customers call us the best roaster in Portland. Free shipping on orders over $50."
)


def facts(
    title: str | None = "Old Coffee Page", meta: str | None = None, body: str = BODY
) -> PageFacts:
    return PageFacts(url="https://e.com/coffee", title=title, meta_description=meta, body_text=body)


def suggestion(
    title: str = GOOD_TITLE, meta: str = GOOD_META, diagnosis: str = "Sells coffee subscriptions"
) -> Suggestion:
    return Suggestion(diagnosis=diagnosis, title=title, meta_description=meta)


def codes(violations: list[Violation]) -> set[str]:
    return {v.code for v in violations}


def text_of_length(n: int) -> str:
    """Exactly n chars, ending on a complete nonsense word so truncation heuristics stay quiet."""
    s = ("coffee beans roasted fresh " * 20)[:n]
    cut = s.rfind(" ")
    s = s[: cut + 1] + "z" * (n - cut - 1)
    return s[:-1] + "z" if s.endswith(" ") else s


def test_clean_suggestion_passes() -> None:
    assert validate(suggestion(), facts()) == []


# --- length bands -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("length", "expected"),
    [(29, {"title_too_short"}), (30, set()), (60, set()), (61, {"title_too_long"})],
)
def test_title_length_boundaries(length: int, expected: set[str]) -> None:
    assert codes(validate(suggestion(title=text_of_length(length)), facts())) == expected


@pytest.mark.parametrize(
    ("length", "expected"),
    [(69, {"meta_too_short"}), (70, set()), (160, set()), (161, {"meta_too_long"})],
)
def test_meta_length_boundaries(length: int, expected: set[str]) -> None:
    assert codes(validate(suggestion(meta=text_of_length(length)), facts())) == expected


def test_200_char_title_is_rejected_with_the_length_in_the_detail() -> None:
    (violation,) = validate(suggestion(title="coffee " * 30), facts())
    assert violation.code == "title_too_long"
    assert violation.field == "title"
    assert "209" in violation.detail  # trailing space is stripped by the schema


def test_empty_strings_are_too_short_not_crashes() -> None:
    found = codes(validate(suggestion(title="", meta="", diagnosis=""), facts()))
    assert {"title_too_short", "meta_too_short", "diagnosis_empty"} <= found


# --- unchanged ----------------------------------------------------------------------------


def test_byte_identical_title_is_rejected() -> None:
    assert codes(validate(suggestion(title=GOOD_TITLE), facts(title=GOOD_TITLE))) == {
        "title_unchanged"
    }


def test_byte_identical_meta_is_rejected() -> None:
    assert codes(validate(suggestion(meta=GOOD_META), facts(meta=GOOD_META))) == {"meta_unchanged"}


def test_case_change_alone_counts_as_changed() -> None:
    assert validate(suggestion(title=GOOD_TITLE.upper()), facts(title=GOOD_TITLE)) == []


# --- invented facts -----------------------------------------------------------------------


def test_numbers_present_in_source_are_allowed() -> None:
    title = "Coffee Beans Shipped to 40 Countries Since 2015"
    assert validate(suggestion(title=title), facts()) == []


def test_number_absent_from_source_is_invented() -> None:
    (v,) = validate(suggestion(title="Coffee Beans Shipped to 90 Countries Weekly"), facts())
    assert v.code == "invented_fact"
    assert "90" in v.detail


def test_price_absent_from_source_is_invented() -> None:
    (v,) = validate(suggestion(title="Fresh Coffee Beans From $9.99 Per Bag Today"), facts())
    assert v.code == "invented_fact"
    assert "$9.99" in v.detail


def test_price_present_in_source_is_allowed() -> None:
    assert validate(suggestion(title="Fresh Coffee Beans From $14.99 Per Bag Today"), facts()) == []


def test_year_absent_from_source_is_invented() -> None:
    (v,) = validate(suggestion(title="Award Coffee Roasters Established In 1998"), facts())
    assert "1998" in v.detail


@pytest.mark.parametrize(
    "claim", ["#1", "guaranteed", "award-winning", "official", "cheapest", "24/7"]
)
def test_claim_phrases_absent_from_source_are_invented(claim: str) -> None:
    title = f"Fresh Coffee Beans, The {claim} Roaster In Town"
    (v,) = validate(suggestion(title=title), facts())
    assert v.code == "invented_fact"
    assert claim.lower() in v.detail.lower()


def test_claim_phrase_present_in_source_is_allowed_case_insensitively() -> None:
    title = "The Best Roaster In Portland: Fresh Coffee Beans"
    assert validate(suggestion(title=title), facts()) == []


def test_free_shipping_needs_source_support() -> None:
    meta = (
        "Free shipping on every bag of freshly roasted single-origin coffee beans delivered weekly."
    )
    assert validate(suggestion(meta=meta), facts()) == []
    (v,) = validate(suggestion(meta=meta), facts(body="We roast coffee beans in small batches."))
    assert v.code == "invented_fact"
    assert v.field == "meta_description"


def test_percentages_are_facts() -> None:
    (v,) = validate(suggestion(title="Save 30% On Fresh Roasted Coffee Beans Today"), facts())
    assert "30%" in v.detail


def test_all_violations_are_reported_together() -> None:
    found = codes(validate(suggestion(title="Best #1 Coffee In 2099", meta=GOOD_META), facts()))
    assert found == {"title_too_short", "invented_fact"}


# --- truncation ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "title",
    [
        "Fresh Roasted Coffee Beans Delivered Weekly -",
        "Fresh Roasted Coffee Beans Delivered Weekly,",
        "Fresh Roasted Coffee Beans Delivered Weekly:",
        "Fresh Roasted Coffee Beans Delivered Weekly...",
        "Fresh Roasted Coffee Beans Delivered Weekly…",
        'Fresh Roasted Coffee Beans "Delivered Weekly',
        "Fresh Roasted Coffee Beans (Delivered Weekly",
        "Fresh Roasted Coffee Beans Delivered Every Wee",
        "Fresh Roasted Coffee Beans Delivered With The",
    ],
)
def test_truncated_titles_are_rejected(title: str) -> None:
    assert "truncated" in codes(validate(suggestion(title=title), facts()))


def test_sentence_ending_punctuation_is_not_truncation() -> None:
    assert (
        validate(suggestion(title="Fresh Roasted Coffee Beans, Delivered Weekly!"), facts()) == []
    )
    assert (
        validate(suggestion(title="Fresh Roasted Coffee Beans (Delivered Weekly)"), facts()) == []
    )


# --- unicode and summary -------------------------------------------------------------------


def test_unicode_is_handled() -> None:
    title = "Café Crème: Frisch Geröstete Kaffeebohnen Wöchentlich"
    body = "Wir rösten Kaffeebohnen für Café Crème und liefern wöchentlich."
    assert validate(suggestion(title=title), facts(body=body)) == []


def test_summary_is_human_readable_for_the_repair_prompt() -> None:
    violations = validate(suggestion(title="Cheapest Coffee", meta="short"), facts())
    text = summarize_violations(violations)
    assert "title" in text and "meta_description" in text
    assert "15" in text  # the actual length is fed back
    assert "cheapest" in text.lower()
