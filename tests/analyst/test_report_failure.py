"""A failure inside the source-failure report always ends the investigation."""

import logging
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from investigator_kit import (
    Scripted,
    candidate,
    events_for,
    make_store,
    reply,
    submit,
    tool_call,
)

from pothole import dailyai
from pothole.analyst import investigate
from pothole.analyst.weather import WeatherUnavailable
from pothole.budget.envelope import DEFAULT_STEPS

OK = "Heat complaints in Brooklyn 3 were 30 against about 3 usual; the high was 3.1 C."
STEPS = (DEFAULT_STEPS[1], DEFAULT_STEPS[2])


def broken(_: object) -> object:
    raise WeatherUnavailable("slow")


def day(db: Path, model: Scripted, report: Any, **kw: Any) -> Any:
    cands = [candidate(12.0, 3), candidate(9.0, 2)]
    spend = dailyai.Spend()
    result = dailyai.investigate_day(
        db, cands, model, cands[0].day, 100, 2, spend, STEPS, 30.0,  # type: ignore[arg-type]
        weather_for=broken, events_for=events_for, on_source_failure=report,
        stop=lambda: False, **kw,
    )  # fmt: skip
    return result, spend


def failing_report(exc: Exception) -> Any:
    def report(label: str, source: Exception) -> None:
        raise exc

    return report


@pytest.mark.parametrize("exc", [RuntimeError("callback broke"), WeatherUnavailable("own type")])
@pytest.mark.parametrize("place", ["tool call", "assembly"])
def test_a_failing_report_reaches_the_step_catch_and_stops_the_run(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, place: str, exc: Exception
) -> None:
    db = make_store(tmp_path / "raw.duckdb")
    if place == "tool call":
        script = [reply(tool_call("weather", {}, "a")), reply(submit(OK, [1]))] * 2
    else:  # the model never asks for weather: the finding fetches it when it is assembled
        script = [reply(tool_call("events", {}, "a")), reply(submit(OK, [1]))] * 2
    model = Scripted(script)
    with caplog.at_level(logging.ERROR):
        result, spend = day(db, model, failing_report(exc))
    assert result.findings == [] and "the investigation failed" in (result.skipped_reason or "")
    assert any(r.getMessage() == "investigation failed" for r in caplog.records)
    assert len(model.sent) == (1 if place == "tool call" else 2)  # nothing after the failure
    assert len(model.script) == (3 if place == "tool call" else 2)  # the second candidate unread
    assert spend.total > Decimal(0)


def test_a_working_report_is_told_and_the_failure_stays_recoverable(tmp_path: Path) -> None:
    db = make_store(tmp_path / "raw.duckdb")
    told: list[str] = []
    model = Scripted(
        [
            reply(tool_call("weather", {}, "a")),
            reply(tool_call("events", {}, "b")),
            reply(submit(OK, [2])),
        ]
    )
    f = investigate.run(
        candidate(), db, model, weather_for=broken, events_for=events_for,  # type: ignore[arg-type]
        on_source_failure=lambda label, exc: told.append(label),
    )  # fmt: skip
    assert told[:1] == ["weather"] and f.weather is None
    first = f.trace[0]["tool_results"][0]["content"]
    assert first.startswith("ref 1: error: unavailable: WeatherUnavailable")
    assert f.status == "investigated" and [e.tool for e in f.evidence] == ["events"]
