"""Token counting, USD pricing, and the run budget."""

from __future__ import annotations

import logging
from collections.abc import Callable

import tiktoken

log = logging.getLogger("seo_scout.ai.cost")

TokenCounter = Callable[[str], int]

PRICING_AS_OF = "2026-09"
# USD per 1M tokens: (prompt, completion). Unknown models are priced at the highest known rate.
PRICING_USD_PER_1M: dict[str, tuple[float, float]] = {
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-2024-08-06": (2.50, 10.00),
    "gpt-4o-2024-11-20": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o-mini-2024-07-18": (0.15, 0.60),
}
_HEURISTIC_CHARS_PER_TOKEN = 4


def price_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    prompt_rate, completion_rate = PRICING_USD_PER_1M.get(model) or max(PRICING_USD_PER_1M.values())
    return (prompt_tokens * prompt_rate + completion_tokens * completion_rate) / 1_000_000


def heuristic_count(text: str) -> int:
    """Roughly four characters per token for English; used when tiktoken is unavailable."""
    return len(text) // _HEURISTIC_CHARS_PER_TOKEN


def tiktoken_counter(model: str) -> TokenCounter:
    """Exact counting via tiktoken, falling back to the heuristic if the encoding can't load.

    The first load downloads the BPE file; on an offline or TLS-intercepted machine that fails,
    and a pre-flight estimate that is roughly right is better than no AI stage at all.
    """
    try:
        try:
            encoding = tiktoken.encoding_for_model(model)
        except KeyError:
            encoding = tiktoken.get_encoding("o200k_base")
    except Exception as exc:
        log.warning("tiktoken unavailable, using heuristic", extra={"error": str(exc)[:200]})
        return heuristic_count
    return lambda text: len(encoding.encode(text))


def estimate_call_usd(
    model: str,
    counter: TokenCounter,
    messages: list[dict[str, str]],
    *,
    max_completion_tokens: int,
) -> float:
    """Pre-flight estimate: counted prompt tokens plus the full completion reservation."""
    prompt_tokens = sum(counter(m["content"]) for m in messages)
    return price_usd(model, prompt_tokens, max_completion_tokens)


class Budget:
    """Hard USD ceiling for one run. Checked before every call, updated after."""

    def __init__(self, max_usd: float) -> None:
        self.max_usd = max_usd
        self.spent = 0.0

    def can_afford(self, usd: float) -> bool:
        return self.spent + usd <= self.max_usd + 1e-9

    def record(self, usd: float) -> None:
        self.spent += usd

    @property
    def remaining(self) -> float:
        return max(0.0, self.max_usd - self.spent)
