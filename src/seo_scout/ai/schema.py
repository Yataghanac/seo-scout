"""The model's output contract: a strict JSON schema for OpenAI Structured Outputs."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Suggestion(BaseModel):
    """What the model must return. Whitespace is stripped; unknown keys are rejected."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    diagnosis: str = Field(description="One sentence: what this page is for and who it serves.")
    title: str = Field(description="Proposed <title>, 30 to 60 characters, not truncated.")
    meta_description: str = Field(
        description="Proposed meta description, 70 to 160 characters, a complete sentence."
    )


def strict_schema() -> dict[str, Any]:
    """Pydantic's schema with the two properties OpenAI strict mode insists on."""
    schema = Suggestion.model_json_schema()
    schema.pop("title", None)
    for prop in schema["properties"].values():
        prop.pop("title", None)
    schema["additionalProperties"] = False
    schema["required"] = list(schema["properties"])
    return schema


def response_format() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {"name": "seo_suggestion", "strict": True, "schema": strict_schema()},
    }
