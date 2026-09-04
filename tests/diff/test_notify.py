import json

import httpx
import respx

from seo_scout.diff.differ import PageSnapshot, RunSnapshot, diff_runs
from seo_scout.diff.notify import post_slack, slack_summary

HOOK = "https://hooks.slack.com/services/T000/B000/XXXX"


def sample_diff():  # type: ignore[no-untyped-def]
    a = RunSnapshot(
        run_id=1, pages={"https://e.com/": PageSnapshot(score=70, rules={"title_missing"})}
    )
    b = RunSnapshot(run_id=2, pages={"https://e.com/": PageSnapshot(score=85, rules=set())})
    return diff_runs(a, b)


def test_summary_is_short_and_informative() -> None:
    text = slack_summary(sample_diff(), "https://e.com/")
    assert "https://e.com/" in text
    assert "70" in text and "85" in text
    assert "1 fixed" in text
    assert len(text) < 600


@respx.mock
async def test_posts_json_payload_and_reports_success(client: httpx.AsyncClient) -> None:
    route = respx.post(HOOK).mock(return_value=httpx.Response(200, text="ok"))
    assert await post_slack(HOOK, "hello", client=client) is True
    assert json.loads(route.calls.last.request.content) == {"text": "hello"}


@respx.mock
async def test_failures_never_raise(client: httpx.AsyncClient) -> None:
    respx.post(HOOK).mock(return_value=httpx.Response(500))
    assert await post_slack(HOOK, "hello", client=client) is False
    respx.post(HOOK).mock(side_effect=httpx.ConnectError("down"))
    assert await post_slack(HOOK, "hello", client=client) is False
