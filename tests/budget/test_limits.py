from decimal import Decimal

from pothole.budget.limits import PRICE, CallLimits, call_cost, call_max_cost


def test_default_limits() -> None:
    assert CallLimits() == CallLimits(12_000, 2_500, "low")


def test_call_maximum_is_0_008() -> None:
    assert call_max_cost(CallLimits()) == Decimal("0.008")


def test_cost_uses_the_per_million_prices() -> None:
    assert call_cost(1_000_000, 0, PRICE) == Decimal("0.25")
    assert call_cost(0, 1_000_000, PRICE) == Decimal("2.00")
