"""A scripted stand-in for the OpenAI client. No network, fully deterministic."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from seo_scout.ai.client import Completion


def completion(
    title: str,
    meta: str,
    diagnosis: str = "Sells coffee subscriptions",
    *,
    prompt_tokens: int = 500,
    completion_tokens: int = 60,
) -> Completion:
    body = {"diagnosis": diagnosis, "title": title, "meta_description": meta}
    return Completion(
        content=json.dumps(body),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


class FakeCompleter:
    """Pops one scripted response (or exception) per call and records every request."""

    def __init__(self, responses: list[Completion | Exception]) -> None:
        self.responses = list(responses)
        self.calls: list[list[dict[str, Any]]] = []
        self.in_flight = 0
        self.max_in_flight = 0

    async def complete(
        self, messages: list[dict[str, Any]], *, response_format: dict[str, Any], max_tokens: int
    ) -> Completion:
        self.calls.append(messages)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(0)  # let other tasks interleave
            if not self.responses:
                raise AssertionError("FakeCompleter ran out of scripted responses")
            item = self.responses.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        finally:
            self.in_flight -= 1
