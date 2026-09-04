import pytest

from seo_scout.ai.cost import Budget, estimate_call_usd, heuristic_count, price_usd


def test_price_for_gpt4o_matches_the_pricing_table() -> None:
    assert price_usd("gpt-4o-2024-08-06", 1_000_000, 0) == pytest.approx(2.50)
    assert price_usd("gpt-4o-2024-08-06", 0, 1_000_000) == pytest.approx(10.00)
    assert price_usd("gpt-4o-2024-08-06", 1000, 100) == pytest.approx(0.0025 + 0.001)


def test_unknown_model_uses_the_most_expensive_known_rate() -> None:
    assert price_usd("gpt-9-preview", 1_000_000, 1_000_000) >= price_usd(
        "gpt-4o", 1_000_000, 1_000_000
    )


def test_heuristic_counter_is_roughly_four_chars_per_token() -> None:
    assert heuristic_count("abcd" * 25) == 25
    assert heuristic_count("") == 0


def test_estimate_uses_the_counter_and_reserves_the_completion() -> None:
    messages = [{"role": "system", "content": "a" * 400}, {"role": "user", "content": "b" * 400}]
    usd = estimate_call_usd(
        "gpt-4o-2024-08-06", heuristic_count, messages, max_completion_tokens=300
    )
    assert usd == pytest.approx(price_usd("gpt-4o-2024-08-06", 200, 300))


def test_budget_gate() -> None:
    budget = Budget(max_usd=0.01)
    assert budget.can_afford(0.004)
    budget.record(0.004)
    assert budget.spent == pytest.approx(0.004)
    assert budget.can_afford(0.006)
    assert not budget.can_afford(0.0061)
    budget.record(0.006)
    assert not budget.can_afford(0.0001)
    assert budget.remaining == pytest.approx(0.0)


def test_zero_budget_affords_nothing() -> None:
    assert not Budget(max_usd=0.0).can_afford(0.000001)
