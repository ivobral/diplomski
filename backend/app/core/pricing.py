"""LLM pricing data + cost helpers.

Single source of truth for per-model $/MTok pricing. Used by:
- ``BenchmarkQuestionResult.cost_usd`` to attach a precise $ figure to
  every benchmark row,
- per-run aggregation in ``metrics.py`` to report total_cost_usd.

Pricing values are intentionally hard-coded with the model snapshot —
provider pricing changes occasionally and a hard-coded table makes the
exact assumption visible in the code (audit-friendly for thesis).
Last updated 2026-06 against published OpenAI rate cards.
"""

# (input_per_1m_usd, output_per_1m_usd) — official OpenAI list pricing.
_PRICING_USD_PER_M: dict[str, tuple[float, float]] = {
    # GPT-4 family
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    # GPT-5 family
    "gpt-5": (1.25, 10.00),
    "gpt-5-mini": (0.25, 2.00),
    "gpt-5-nano": (0.05, 0.40),
    "gpt-5.1": (1.25, 10.00),
    "gpt-5.2": (1.75, 14.00),
    "gpt-5.4-mini": (0.75, 4.50),
    "gpt-5.4": (2.50, 15.00),
    "gpt-5.5": (5.00, 30.00),
    # o-series
    "o4-mini": (1.10, 4.40),
    "o3": (2.00, 8.00),
    "o3-mini": (1.10, 4.40),
    "o1": (15.00, 60.00),
    "o1-mini": (1.10, 4.40),
}


def estimate_cost_usd(
    model: str,
    input_tokens: int | None,
    output_tokens: int | None,
) -> float | None:
    """Compute $ cost for one LLM response.

    Returns None if the model isn't in the pricing table or token counts
    are missing — caller treats None as "unknown cost", not zero.
    """

    if input_tokens is None or output_tokens is None:
        return None
    pricing = _PRICING_USD_PER_M.get(model)
    if pricing is None:
        return None
    in_price, out_price = pricing
    return (input_tokens * in_price + output_tokens * out_price) / 1_000_000


def is_known_model(model: str) -> bool:
    """True if we have pricing for this model name."""

    return model in _PRICING_USD_PER_M
