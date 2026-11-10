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
from pothole.analyst.finding import to_json
from pothole.llm.client import CallFailed, Usage

OK = "Heat complaints in Brooklyn 3 were 30 against about 3 usual; the high was 3.1 C."


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return make_store(tmp_path / "raw.duckdb")


def go(model: Scripted, db: Path, **kw):  # type: ignore[no-untyped-def]
    return investigate.run(
        candidate(),
        db,
        model,
        weather_for=weather_for,
        events_for=events_for,
        **kw,  # type: ignore[arg-type]
    )


def test_scripted_investigation_ends_in_a_finding(db: Path) -> None:
    model = Scripted(
        [
            reply(
                tool_call("weather", {}, "w1"),
                tool_call("breakdown", {"by": "descriptor", "top": 3}, "b1"),
            ),
            reply(submit(OK, ["w1", "b1"])),
        ]
    )
    f = go(model, db)
    assert f.id == "2026-11-07-brooklyn-3-heat-hot-water"
    assert f.summary == OK and [e.tool for e in f.evidence] == ["weather", "breakdown"]
    assert f.evidence[0].args == {} and "3.1" in f.evidence[0].excerpt
    assert len(f.series) == 90 and f.series[-1][1] == 30 and 0 < len(f.points) <= 500
    assert f.weather is not None and f.weather.tmax_c == 3.1 and f.events[0].name == "Street fair"
    assert str(f.cost) == "0.002" and len(f.trace) == 2
    assert to_json(f)["trace"][1]["tool_results"][0]["content"] == "accepted"
    assert "caused" not in model.sent[0]["messages"][1]["content"]


@pytest.mark.parametrize(
    "bad", ["It was Caused by cold.", "Rise because of rain.", "Up DUE TO a fair."]
)
def test_causal_wording_is_rejected_and_counts_as_a_call(db: Path, bad: str) -> None:
    model = Scripted(
        [
            reply(tool_call("weather", {}, "w1")),
            reply(submit(bad, ["w1"])),
            reply(submit(OK, ["w1"])),
        ]
    )
    f = go(model, db)
    assert f.summary == OK and len(f.trace) == 3
    refused = f.trace[1]["tool_results"][0]["content"]
    assert refused.startswith("error: the summary says")
    assert "associated with" in refused


def test_evidence_must_name_tool_calls_that_were_made(db: Path) -> None:
    model = Scripted(
        [
            reply(tool_call("weather", {}, "w1")),
            reply(submit(OK, ["w1", "zz"])),
            reply(submit(OK, ["w1"])),
        ]
    )
    f = go(model, db)
    assert "were not made: zz" in f.trace[1]["tool_results"][0]["content"]
    assert [e.tool for e in f.evidence] == ["weather"]


def test_a_failed_tool_call_cannot_be_cited(db: Path) -> None:
    model = Scripted(
        [
            reply(tool_call("series", {"days": 500}, "x1")),
            *[reply(submit(OK, ["x1"])) for _ in range(4)],
        ]
    )
    f = go(model, db)
    assert f.evidence == [] and "did not finish" in f.summary


def test_wording_check_must_be_true(db: Path) -> None:
    model = Scripted(
        [
            reply(tool_call("weather", {}, "w1")),
            reply(submit(OK, ["w1"], check=False)),
            reply(submit(OK, ["w1"])),
        ]
    )
    f = go(model, db)
    assert f.summary == OK and "wording_check" in f.trace[1]["tool_results"][0]["content"]


def test_the_submit_tool_is_forced_when_two_calls_remain(db: Path) -> None:
    model = Scripted(
        [
            reply(tool_call("weather", {}, "w1")),
            reply(tool_call("events", {}, "e1")),
            reply(tool_call("neighbours", {}, "n1")),
            reply(submit(OK, ["w1"])),
        ]
    )
    f = go(model, db)
    assert [s["tool_choice"] for s in model.sent] == [None, None, None, investigate.FORCE]
    assert "2 calls left" in model.sent[3]["messages"][-1]["content"]
    assert f.summary == OK


def test_no_submission_in_five_calls_still_returns_the_counts(db: Path) -> None:
    model = Scripted([reply(tool_call("weather", {}, f"w{i}")) for i in range(5)])
    f = go(model, db)
    assert len(model.sent) == 5 and "did not finish" in f.summary and f.evidence == []
    assert f.observed == 30 and len(f.series) == 90 and f.cost > 0


def test_a_failed_model_call_is_charged_and_ends_the_run(db: Path) -> None:
    model = Scripted(
        [
            CallFailed(
                "no response; charged the maximum",
                Usage(0, 0, __import__("decimal").Decimal("0.008")),
            )
        ]
    )
    f = go(model, db)
    assert str(f.cost) == "0.008" and "did not finish" in f.summary


def test_weather_failure_leaves_the_finding_without_weather(db: Path) -> None:
    def broken(_: object) -> object:
        raise TimeoutError("slow")

    model = Scripted([reply(tool_call("events", {}, "e1")), reply(submit(OK, ["e1"]))])
    f = investigate.run(candidate(), db, model, weather_for=broken, events_for=events_for)  # type: ignore[arg-type]
    assert f.weather is None and len(f.events) == 1


def test_day_investigates_the_top_two_by_ratio(db: Path) -> None:
    script = [reply(tool_call("weather", {}, "w1")), reply(submit(OK, ["w1"]))] * 2
    model = Scripted(script)
    cands = [candidate(5.0, 1), candidate(12.0, 3), candidate(9.0, 2)]
    result = investigate.day(
        db,
        cands,
        model,
        analysed=cands[0].day,
        tested=100,
        cleared=3,  # type: ignore[arg-type]
        weather_for=weather_for,
        events_for=events_for,
    )
    assert [f.board for f in result.findings] == [3, 2]
    assert (result.tested, result.cleared) == (100, 3)


def test_no_candidates_means_no_findings_and_no_model_call(db: Path) -> None:
    model = Scripted([])
    result = investigate.day(db, [], model, analysed=candidate().day, tested=100, cleared=0)  # type: ignore[arg-type]
    assert result.findings == [] and model.sent == []
