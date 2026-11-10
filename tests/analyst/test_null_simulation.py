"""Regression guards on the measured any-flag rates (seed 7), not claims about real data."""

import time

import pytest

from pothole.analyst import null_simulation as sim


@pytest.mark.parametrize(
    ("spread", "low", "high"),
    [(1.0, 0.0, 0.02), (1.5, 0.0, 0.096), (2.0, 0.14, 0.21), (3.0, 0.28, 0.36)],
)
def test_measured_any_flag_rate(spread: float, low: float, high: float) -> None:
    started = time.monotonic()
    rate = sim.any_flag_rate(spread)
    assert low <= rate <= high
    assert time.monotonic() - started < 20
