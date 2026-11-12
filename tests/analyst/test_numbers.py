import json
from pathlib import Path

import pytest

from pothole.analyst.numbers import one_decimal

CASES = json.loads(
    (Path(__file__).parents[2] / "web" / "scripts" / "one-decimal-cases.json").read_text()
)


@pytest.mark.parametrize(("value", "shown"), CASES)
def test_one_decimal_rounds_half_away_from_zero(value: float, shown: str) -> None:
    assert one_decimal(value) == shown


def test_the_model_is_told_the_numbers_the_card_shows() -> None:
    from datetime import date

    from pothole.analyst.detect import Candidate
    from pothole.analyst.loop import _brief

    c = Candidate("Queens", 4, "NOISE", date(2026, 11, 4), 47, 3.25, 14.46, 1e-9, 1e-7)
    assert "about 3.3 usual for that weekday (14.5 times)" in _brief(c)
