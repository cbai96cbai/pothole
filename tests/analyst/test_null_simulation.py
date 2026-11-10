"""Exact guards on the quiet-day rates (seed 7): they must equal data/null_simulation.json."""

import json

import pytest

from pothole.analyst import null_simulation as sim

PUBLISHED = json.loads(sim.PATH.read_text())["share_of_days_with_a_candidate"]


@pytest.mark.parametrize("spread", sim.SPREADS)
def test_rate_equals_the_published_value(spread: float) -> None:
    rate, tested = sim.any_flag_rate(spread)
    assert tested == 193
    assert round(rate, 4) == PUBLISHED[f"{spread:g}x"]


@pytest.mark.parametrize("spread", [2.0, 3.0])
def test_full_rule_is_stricter_than_the_filter_alone(spread: float) -> None:
    full, _ = sim.any_flag_rate(spread)
    bh_only, _ = sim.any_flag_rate(spread, bh_only=True)
    assert full < bh_only
