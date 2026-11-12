"""A ref is an int or a digit string of at most six characters; anything longer is not a ref."""

from pathlib import Path

import pytest
from investigator_kit import (
    Scripted,
    candidate,
    events_for,
    make_store,
    reply,
    submit,
    tool_call,
    weather_for,
)

from pothole.analyst import investigate
from pothole.analyst.refs import _number

OK = "Heat complaints in Brooklyn 3 were 30 against about 3 usual; the high was 3.1 C."


def test_number_accepts_six_digits_and_refuses_seven() -> None:
    assert _number("999999") == 999999 and _number("000001") == 1 and _number(7) == 7
    assert _number("1234567") is None and _number("9" * 4301) is None
    assert _number("12a") is None and _number("") is None and _number(True) is None


@pytest.mark.parametrize("digits", ["1234567", "9" * 4301])
def test_a_long_digit_string_as_evidence_is_refused_with_the_list(
    tmp_path: Path, digits: str
) -> None:
    db = make_store(tmp_path / "raw.duckdb")
    model = Scripted(
        [
            reply(tool_call("weather", {}, "a")),
            reply(submit(OK, [digits])),
            reply(submit(OK, [1])),
        ]
    )
    f = investigate.run(candidate(), db, model, weather_for=weather_for, events_for=events_for)
    refusal = f.trace[1]["tool_results"][0]["content"]
    assert refusal.startswith("error: ") and "refs you can cite: 1 weather" in refusal
    assert f.status == "investigated"
