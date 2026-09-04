"""Checks the model's work. Pure functions; every rejection carries a specific reason.

Nothing the model produces reaches the user without passing `validate`. The checks are
deliberately mechanical (lengths, byte equality, substring presence, punctuation) so that a
non-author can read them and predict exactly what will be rejected.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel

from seo_scout.ai.schema import Suggestion

TITLE_RANGE = (30, 60)
META_RANGE = (70, 160)
DIAGNOSIS_MAX = 300

Field = Literal["title", "meta_description", "diagnosis"]

# Facts a model is tempted to invent: money, quantities, dates, percentages, and marketing claims.
_NUMBER = re.compile(r"[$€£]\s?\d[\d,]*(?:\.\d+)?%?|\d[\d,]*(?:\.\d+)?%?")
_CLAIMS = (
    "#1",
    "no. 1",
    "number one",
    "best",
    "free shipping",
    "free delivery",
    "guaranteed",
    "guarantee",
    "award-winning",
    "award winning",
    "official",
    "cheapest",
    "lowest price",
    "24/7",
    "money-back",
    "certified",
    "world's",
    "top-rated",
    "top rated",
    "fastest",
    "largest",
    "leading",
)
_DANGLING_ENDINGS = ("-", ",", ":", ";", "(", "[", "{", "...", "…", "&", "/")
_DANGLING_WORDS = frozenset(
    [
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "with",
        "for",
        "to",
        "in",
        "on",
        "at",
        "by",
        "from",
        "your",
        "our",
        "their",
        "its",
    ]
)
_WORD = re.compile(r"\w+", re.UNICODE)


class PageFacts(BaseModel):
    """What the page actually says. The only ground truth the validator trusts."""

    url: str
    title: str | None
    meta_description: str | None
    body_text: str

    @property
    def source_text(self) -> str:
        return " ".join(p for p in (self.title, self.meta_description, self.body_text) if p)


class Violation(BaseModel):
    field: Field
    code: str
    detail: str


def validate(suggestion: Suggestion, facts: PageFacts) -> list[Violation]:
    """Every problem with the suggestion, so the repair prompt can list them all at once."""
    violations: list[Violation] = []
    violations += _check_length("title", suggestion.title, TITLE_RANGE)
    violations += _check_length("meta_description", suggestion.meta_description, META_RANGE)
    violations += _check_diagnosis(suggestion.diagnosis)
    violations += _check_unchanged(suggestion, facts)
    source = facts.source_text
    checked: list[tuple[Field, str]] = [
        ("title", suggestion.title),
        ("meta_description", suggestion.meta_description),
    ]
    for field, text in checked:
        violations += _check_invented(field, text, source)
        violations += _check_truncated(field, text, source)
    return violations


def summarize_violations(violations: list[Violation]) -> str:
    """Human-readable list fed back to the model on the single repair attempt."""
    return "\n".join(f"- {v.field}: {v.code} ({v.detail})" for v in violations)


def _check_length(field: Field, text: str, bounds: tuple[int, int]) -> list[Violation]:
    low, high = bounds
    n = len(text)
    prefix = "title" if field == "title" else "meta"
    if n < low:
        return [
            Violation(
                field=field, code=f"{prefix}_too_short", detail=f"{n} characters (minimum {low})"
            )
        ]
    if n > high:
        return [
            Violation(
                field=field, code=f"{prefix}_too_long", detail=f"{n} characters (maximum {high})"
            )
        ]
    return []


def _check_diagnosis(text: str) -> list[Violation]:
    if not text:
        return [Violation(field="diagnosis", code="diagnosis_empty", detail="no diagnosis given")]
    if len(text) > DIAGNOSIS_MAX:
        return [
            Violation(
                field="diagnosis",
                code="diagnosis_too_long",
                detail=f"{len(text)} characters (maximum {DIAGNOSIS_MAX})",
            )
        ]
    return []


def _check_unchanged(suggestion: Suggestion, facts: PageFacts) -> list[Violation]:
    found = []
    if facts.title is not None and suggestion.title == facts.title:
        found.append(
            Violation(
                field="title", code="title_unchanged", detail="identical to the current title"
            )
        )
    if facts.meta_description is not None and suggestion.meta_description == facts.meta_description:
        found.append(
            Violation(
                field="meta_description",
                code="meta_unchanged",
                detail="identical to the current meta description",
            )
        )
    return found


def _squash(text: str) -> str:
    return text.casefold().replace(",", "").replace(" ", "")


def _check_invented(field: Field, text: str, source: str) -> list[Violation]:
    """Numbers, prices, years, percentages and claim phrases must already appear on the page."""
    invented: list[str] = []
    squashed_source = _squash(source)
    for token in _NUMBER.findall(text):
        if _squash(token) not in squashed_source:
            invented.append(token)
    lowered = text.casefold()
    lowered_source = source.casefold()
    for claim in _CLAIMS:
        pattern = rf"(?<![\w-]){re.escape(claim)}(?![\w-])"
        if re.search(pattern, lowered) and not re.search(pattern, lowered_source):
            invented.append(claim)
    if not invented:
        return []
    listed = ", ".join(f'"{t}"' for t in invented)
    return [Violation(field=field, code="invented_fact", detail=f"{listed} not found on the page")]


def _check_truncated(field: Field, text: str, source: str) -> list[Violation]:
    if not text:
        return []
    reason = _truncation_reason(text, source)
    return [Violation(field=field, code="truncated", detail=reason)] if reason else []


def _truncation_reason(text: str, source: str) -> str | None:
    stripped = text.rstrip()
    if stripped.endswith(_DANGLING_ENDINGS):
        return f"ends with {stripped[-1]!r}"
    if stripped.count('"') % 2 or stripped.count("(") != stripped.count(")"):
        return "unbalanced quotes or parentheses"
    words = _WORD.findall(stripped.casefold())
    if not words:
        return None
    last = words[-1]
    if last in _DANGLING_WORDS:
        return f"ends on the function word {last!r}"
    source_words = set(_WORD.findall(source.casefold()))
    if last not in source_words and any(w != last and w.startswith(last) for w in source_words):
        return f"last word {last!r} looks cut off"
    return None
