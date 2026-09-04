"""The OpenAI wrapper, exercised against a mocked HTTP endpoint. No network."""

import json

import httpx
import pytest
import respx

from seo_scout.ai.client import AIUnavailable, OpenAICompleter, make_completer
from seo_scout.ai.schema import response_format
from seo_scout.config import Settings

ENDPOINT = "https://api.openai.com/v1/chat/completions"
MESSAGES = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]


class Sleeps:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def ok_body(
    content: str = '{"diagnosis":"d","title":"t","meta_description":"m"}',
) -> dict[str, object]:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 0,
        "model": "gpt-4o-2024-08-06",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content, "refusal": None},
            }
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19},
    }


def make(client: httpx.AsyncClient, sleeps: Sleeps) -> OpenAICompleter:
    return OpenAICompleter("sk-test", "gpt-4o-2024-08-06", sleep=sleeps, http_client=client)


@respx.mock
async def test_success_returns_content_and_usage(client: httpx.AsyncClient) -> None:
    route = respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json=ok_body()))
    completion = await make(client, Sleeps()).complete(
        MESSAGES, response_format=response_format(), max_tokens=300
    )
    assert completion.content is not None and json.loads(completion.content)["title"] == "t"
    assert (completion.prompt_tokens, completion.completion_tokens) == (12, 7)
    sent = json.loads(route.calls.last.request.content)
    assert sent["response_format"]["type"] == "json_schema"
    assert sent["response_format"]["json_schema"]["strict"] is True
    assert sent["max_completion_tokens"] == 300
    assert route.calls.last.request.headers["authorization"] == "Bearer sk-test"


@respx.mock
async def test_rate_limit_backs_off_then_succeeds(client: httpx.AsyncClient) -> None:
    respx.post(ENDPOINT).mock(
        side_effect=[
            httpx.Response(429, json={"error": {"message": "slow down"}}),
            httpx.Response(429, json={"error": {"message": "slow down"}}),
            httpx.Response(200, json=ok_body()),
        ]
    )
    sleeps = Sleeps()
    completion = await make(client, sleeps).complete(
        MESSAGES, response_format=response_format(), max_tokens=300
    )
    assert completion.content is not None
    assert sleeps.calls == [1.0, 2.0]


@respx.mock
async def test_persistent_rate_limit_becomes_unavailable(client: httpx.AsyncClient) -> None:
    route = respx.post(ENDPOINT).mock(
        return_value=httpx.Response(429, json={"error": {"message": "slow down"}})
    )
    with pytest.raises(AIUnavailable, match="rate limited"):
        await make(client, Sleeps()).complete(
            MESSAGES, response_format=response_format(), max_tokens=300
        )
    assert route.call_count == 3


@respx.mock
async def test_bad_key_is_unavailable(client: httpx.AsyncClient) -> None:
    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(401, json={"error": {"message": "Incorrect API key"}})
    )
    with pytest.raises(AIUnavailable, match="authentication"):
        await make(client, Sleeps()).complete(
            MESSAGES, response_format=response_format(), max_tokens=300
        )


@respx.mock
async def test_outage_and_timeout_are_unavailable(client: httpx.AsyncClient) -> None:
    respx.post(ENDPOINT).mock(return_value=httpx.Response(503, json={"error": {"message": "down"}}))
    with pytest.raises(AIUnavailable, match="503"):
        await make(client, Sleeps()).complete(
            MESSAGES, response_format=response_format(), max_tokens=300
        )
    respx.post(ENDPOINT).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(AIUnavailable, match="timed out"):
        await make(client, Sleeps()).complete(
            MESSAGES, response_format=response_format(), max_tokens=300
        )


@respx.mock
async def test_refusal_is_surfaced_not_raised(client: httpx.AsyncClient) -> None:
    body = ok_body()
    body["choices"][0]["message"] = {"role": "assistant", "content": None, "refusal": "no"}  # type: ignore[index]
    respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json=body))
    completion = await make(client, Sleeps()).complete(
        MESSAGES, response_format=response_format(), max_tokens=300
    )
    assert completion.content is None
    assert completion.refusal == "no"


def test_make_completer_is_none_without_a_key() -> None:
    assert make_completer(Settings()) is None
    assert make_completer(Settings(openai_api_key="sk-x")) is not None
