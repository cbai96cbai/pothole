"""The most one daily run may spend, computed from its configuration.

The run refuses to start when the envelope is above the ceiling: the monthly limit over 31 days.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from pothole.budget.limits import DEFAULT_LIMITS, PRICE, CallLimits, Price, call_max_cost

MONTHLY_LIMIT = Decimal("4")
CEILING_DAYS = 31
CEILING = MONTHLY_LIMIT / CEILING_DAYS  # what one run may spend: the month's limit over its days


class EnvelopeExceeded(Exception):
    """The configured run could spend more than the ceiling."""


@dataclass(frozen=True)
class Step:
    name: str
    calls: int


# one repair-or-drill slot of at most 6 calls, then up to 2 investigations of at most 5 calls
DEFAULT_STEPS = (Step("repair_or_drill", 6), Step("investigation_1", 5), Step("investigation_2", 5))


@dataclass(frozen=True)
class RunConfig:
    steps: Sequence[Step] = DEFAULT_STEPS
    limits: CallLimits = DEFAULT_LIMITS
    price: Price = PRICE
    ceiling: Decimal = CEILING


def run_envelope(cfg: RunConfig | None = None) -> Decimal:
    cfg = cfg or RunConfig()
    maximum = call_max_cost(cfg.limits, cfg.price)
    total = sum((step.calls * maximum for step in cfg.steps), Decimal(0))
    if total > cfg.ceiling:
        raise EnvelopeExceeded(f"run envelope {total} exceeds the ceiling {cfg.ceiling}")
    return total
