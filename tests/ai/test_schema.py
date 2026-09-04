import pytest

from seo_scout.ai.schema import Suggestion, response_format


def test_response_format_is_strict_json_schema() -> None:
    fmt = response_format()
    assert fmt["type"] == "json_schema"
    spec = fmt["json_schema"]
    assert spec["strict"] is True
    schema = spec["schema"]
    assert schema["additionalProperties"] is False
    assert (
        set(schema["required"])
        == set(schema["properties"])
        == {
            "diagnosis",
            "title",
            "meta_description",
        }
    )
    assert all(p["type"] == "string" for p in schema["properties"].values())


def test_suggestion_strips_whitespace_and_forbids_extras() -> None:
    s = Suggestion.model_validate_json(
        '{"diagnosis": " d ", "title": " t ", "meta_description": " m "}'
    )
    assert (s.diagnosis, s.title, s.meta_description) == ("d", "t", "m")
    with pytest.raises(ValueError):
        Suggestion.model_validate_json(
            '{"diagnosis": "d", "title": "t", "meta_description": "m", "x": 1}'
        )


def test_invalid_json_raises_value_error() -> None:
    with pytest.raises(ValueError):
        Suggestion.model_validate_json("Sure! Here is a title: ...")
