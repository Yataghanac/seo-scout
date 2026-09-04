"""Thin wrapper over the OpenAI SDK. Every failure mode becomes AIUnavailable."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, cast

import openai
from openai import AsyncOpenAI
from pydantic import BaseModel

from seo_scout.config import Settings

log = logging.getLogger("seo_scout.ai.client")

SleepFn = Callable[[float], Awaitable[None]]
RATE_LIMIT_TRIES = 3
REQUEST_TIMEOUT_S = 30.0
TEMPERATURE = 0.3


class AIUnavailable(Exception):
    """Auth failure, connection loss, timeout, outage, or persistent rate limiting."""


class Completion(BaseModel):
    content: str | None
    refusal: str | None = None
    prompt_tokens: int
    completion_tokens: int


class Completer(Protocol):
    async def complete(
        self, messages: list[dict[str, str]], *, response_format: dict[str, Any], max_tokens: int
    ) -> Completion: ...


class OpenAICompleter:
    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        sleep: SleepFn = asyncio.sleep,
        http_client: Any = None,  # an httpx.AsyncClient; the SDK types its own alias
    ) -> None:
        self._client = AsyncOpenAI(
            api_key=api_key, timeout=REQUEST_TIMEOUT_S, max_retries=0, http_client=http_client
        )
        self._model = model
        self._sleep = sleep

    async def complete(
        self, messages: list[dict[str, str]], *, response_format: dict[str, Any], max_tokens: int
    ) -> Completion:
        delay = 1.0
        for attempt in range(1, RATE_LIMIT_TRIES + 1):
            try:
                return await self._once(messages, response_format, max_tokens)
            except openai.RateLimitError as exc:
                if attempt == RATE_LIMIT_TRIES:
                    raise AIUnavailable(f"rate limited after {attempt} tries: {exc}") from exc
                log.warning("rate limited, backing off", extra={"try": attempt, "wait": delay})
                await self._sleep(delay)
                delay *= 2
            except openai.AuthenticationError as exc:
                raise AIUnavailable(f"authentication failed: {exc}") from exc
            except openai.APITimeoutError as exc:
                raise AIUnavailable(f"request timed out: {exc}") from exc
            except openai.APIConnectionError as exc:
                raise AIUnavailable(f"connection failed: {exc}") from exc
            except openai.APIStatusError as exc:
                raise AIUnavailable(f"API error {exc.status_code}: {exc.message}") from exc
        raise AssertionError("unreachable")

    async def _once(
        self, messages: list[dict[str, str]], response_format: dict[str, Any], max_tokens: int
    ) -> Completion:
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=cast(Any, messages),
            response_format=cast(Any, response_format),
            max_completion_tokens=max_tokens,
            temperature=TEMPERATURE,
        )
        choice = response.choices[0]
        usage = response.usage
        return Completion(
            content=choice.message.content,
            refusal=getattr(choice.message, "refusal", None),
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
        )


def make_completer(settings: Settings) -> Completer | None:
    """None when there is no API key: the caller degrades to deterministic results."""
    if not settings.openai_api_key:
        return None
    return OpenAICompleter(settings.openai_api_key, settings.openai_model)
