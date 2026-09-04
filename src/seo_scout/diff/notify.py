"""Optional Slack summary: one httpx POST to an incoming webhook. Never raises."""

from __future__ import annotations

import logging

import httpx

from seo_scout.diff.differ import RunDiff

log = logging.getLogger("seo_scout.diff.notify")

TIMEOUT_S = 10.0


def slack_summary(d: RunDiff, site: str) -> str:
    delta = d.average_after - d.average_before
    return (
        f"SEO Scout: {site} run {d.run_a} -> {d.run_b}. "
        f"Average score {d.average_before:g} -> {d.average_after:g} ({delta:+g}). "
        f"Pages {d.pages_before} -> {d.pages_after} "
        f"(+{len(d.pages_added)} / -{len(d.pages_removed)}). "
        f"Issues: {len(d.issues_fixed)} fixed, {len(d.issues_introduced)} introduced."
    )


async def post_slack(
    webhook_url: str, text: str, *, client: httpx.AsyncClient | None = None
) -> bool:
    """True on a 2xx. Any failure is logged and returns False; a report never fails on Slack."""
    own = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT_S)
    try:
        response = await client.post(webhook_url, json={"text": text})
    except httpx.HTTPError as exc:
        log.warning("slack post failed", extra={"error": str(exc)})
        return False
    finally:
        if own:
            await client.aclose()
    if response.is_success:
        return True
    log.warning("slack post rejected", extra={"status": response.status_code})
    return False
