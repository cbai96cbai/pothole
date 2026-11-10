"""Regression guards on the measured any-flag rates (seed 7), not claims about real data."""

import pytest

from pothole.analyst import null_simulation as sim


@pytest.mark.parametrize(
    ("spread", "low", "high"),
    [(1.0, 0.0, 0.02), (1.5, 0.0, 0.096), (2.0, 0.13, 0.20), (3.0, 0.26, 0.34)],
)
def test_measured_any_flag_rate(spread: float, low: float, high: float) -> None:
    rate, tested = sim.any_flag_rate(spread)
    assert tested == 193
    assert low <= rate <= high
