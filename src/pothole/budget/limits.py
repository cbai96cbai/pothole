"""Per-call limits, the price and the maximum cost of one call."""

from dataclasses import dataclass
from decimal import Decimal

MILLION = Decimal(1_000_000)


@dataclass(frozen=True)
class Price:
    """Dollars per 1M tokens."""

    input: Decimal
    output: Decimal


# gpt-5-mini; reasoning tokens are output tokens
PRICE = Price(Decimal("0.25"), Decimal("2.00"))


@dataclass(frozen=True)
class CallLimits:
    input_tokens: int = 12_000
    completion_tokens: int = 2_500
    reasoning_effort: str | None = "low"


DEFAULT_LIMITS = CallLimits()


def call_cost(prompt_tokens: int, completion_tokens: int, price: Price = PRICE) -> Decimal:
    """Cost of a call; completion tokens already include reasoning tokens."""
    return Decimal(prompt_tokens) * price.input / MILLION + (
        Decimal(completion_tokens) * price.output / MILLION
    )


def call_max_cost(limits: CallLimits, price: Price = PRICE) -> Decimal:
    return call_cost(limits.input_tokens, limits.completion_tokens, price)
