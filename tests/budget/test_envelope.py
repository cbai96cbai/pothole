from decimal import Decimal

import pytest

from pothole.budget.envelope import (
    CEILING,
    CEILING_DAYS,
    MONTHLY_LIMIT,
    EnvelopeExceeded,
    RunConfig,
    Step,
    run_envelope,
)


def test_envelope_is_0_128_and_refuses_above_0_129() -> None:
    assert run_envelope(RunConfig()) == Decimal("0.128")
    assert run_envelope() == Decimal("0.128")
    assert abs(CEILING * CEILING_DAYS - MONTHLY_LIMIT) < Decimal("1e-20")
    assert run_envelope() == Decimal("0.128") <= CEILING
    # one more call: 17 calls = 0.136
    with pytest.raises(EnvelopeExceeded):
        run_envelope(RunConfig(steps=(Step("slot", 7), Step("inv", 5), Step("inv", 5))))


def test_envelope_sums_calls_times_the_maximum() -> None:
    assert run_envelope(RunConfig(steps=(Step("a", 1), Step("b", 2)))) == Decimal("0.024")
    assert run_envelope(RunConfig(steps=())) == Decimal(0)
