"""Evidence is cited by the numbers the model was shown: `ref <n>:` at the start of each result."""

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
from pothole.analyst.tools import Toolbox
from pothole.llm.client import CallFailed, Usage
from pothole.repair.validate import ToolError

OK = "Heat complaints in Brooklyn 3 were 30 against about 3 usual; the high was 3.1 C."
LIST_3 = "refs you can cite: 1 series, 2 breakdown, 3 weather"


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return make_store(tmp_path / "raw.duckdb")


def go(model: Scripted, db: Path, **kw):  # type: ignore[no-untyped-def]
    kw = {"weather_for": weather_for, "events_for": events_for, **kw}
    return investigate.run(candidate(), db, model, **kw)


def three_tools() -> list:  # type: ignore[type-arg]
    return [
        tool_call("series", {"days": 5}, "a"),
        tool_call("breakdown", {"by": "descriptor", "top": 3}, "b"),
        tool_call("weather", {}, "c"),
    ]


def shown(f, call: int, n: int = 0) -> str:  # type: ignore[no-untyped-def]
    return f.trace[call - 1]["tool_results"][n]["content"]


def test_results_start_with_their_number_and_names_get_a_list(db: Path) -> None:
    model = Scripted(
        [
            reply(*three_tools()),
            reply(submit(OK, ["functions.series", "weather"])),
            reply(submit(OK, [1, 3])),
        ]
    )
    f = go(model, db)
    assert [shown(f, 1, i).split(":")[0] for i in range(3)] == ["ref 1", "ref 2", "ref 3"]
    assert shown(f, 2).startswith("error: ") and LIST_3 in shown(f, 2)
    assert f.status == "investigated" and [e.tool for e in f.evidence] == ["series", "weather"]
    assert not any(e.excerpt.startswith("ref ") for e in f.evidence)


def test_numbers_map_to_the_right_call_when_a_tool_runs_twice(db: Path) -> None:
    model = Scripted(
        [
            reply(tool_call("series", {"days": 2}, "a"), tool_call("series", {"days": 4}, "b")),
            reply(submit(OK, ["2", 1, 2])),
        ]
    )
    f = go(model, db)
    assert [(e.tool, e.args) for e in f.evidence] == [
        ("series", {"days": 2}),
        ("series", {"days": 4}),
    ]  # in ref order, not in the order the model cited them
    assert len(f.evidence[0].excerpt) < len(f.evidence[1].excerpt)


@pytest.mark.parametrize("bad", [[9], [0], [True], [1.5], ["1a"], [[1]], [], "1"])
def test_an_unknown_or_malformed_number_is_refused_with_the_list(db: Path, bad: object) -> None:
    model = Scripted(
        [reply(*three_tools()), reply(submit(OK, bad)), reply(submit(OK, [2]))]  # type: ignore[arg-type]
    )
    f = go(model, db)
    assert LIST_3 in shown(f, 2) and f.status == "investigated"
    assert [e.tool for e in f.evidence] == ["breakdown"]


def test_a_failed_attempt_takes_a_number_but_is_not_listed_or_citable(db: Path) -> None:
    model = Scripted(
        [
            reply(tool_call("series", {"days": 500}, "a"), tool_call("weather", {}, "b")),
            reply(submit(OK, [1])),
            reply(submit(OK, [2])),
        ]
    )
    f = go(model, db)
    assert shown(f, 1, 0).startswith("ref 1: error: ") and shown(f, 1, 1).startswith("ref 2: ")
    assert "refs you can cite: 2 weather" in shown(f, 2) and "1 series" not in shown(f, 2)
    assert [e.tool for e in f.evidence] == ["weather"]


def test_only_refs_from_earlier_replies_can_be_cited(db: Path) -> None:
    same_reply = reply(tool_call("weather", {}, "a"), submit(OK, [1, 2]))
    model = Scripted(
        [reply(tool_call("series", {"days": 3}, "x")), same_reply, reply(submit(OK, [1, 2]))]
    )
    f = go(model, db)
    second = shown(f, 2, 1)
    assert second.startswith("error: ") and "separate reply" in second
    assert "refs you can cite: 1 series" in second and "weather" not in second.split("cite:")[1]
    assert f.status == "investigated" and [e.tool for e in f.evidence] == ["series", "weather"]


def test_submit_listed_before_the_tools_in_one_reply_cannot_cite_them(db: Path) -> None:
    model = Scripted(
        [reply(submit(OK, [1]), tool_call("weather", {}, "a")), reply(submit(OK, [1]))]
    )
    f = go(model, db)
    assert "no refs you can cite yet" in shown(f, 1, 0) and f.status == "investigated"


def test_two_submissions_in_one_reply_only_the_first_counts(db: Path) -> None:
    model = Scripted(
        [reply(tool_call("weather", {}, "a")), reply(submit(OK, [1]), submit(OK, [1]))]
    )
    f = go(model, db)
    assert shown(f, 2, 0) == "accepted" and shown(f, 2, 1) == "ignored: one submission per reply"
    refused_first = Scripted(
        [
            reply(tool_call("weather", {}, "a")),
            reply(submit(OK, [7]), submit(OK, [1])),
            reply(submit(OK, [1])),
        ]
    )
    g = go(refused_first, db)
    assert shown(g, 2, 1) == "ignored: one submission per reply" and g.status == "investigated"


def test_replay_of_a_refused_live_submission_now_lists_the_refs(db: Path) -> None:
    live = {  # arguments of the refused submission in the 2026-11-04 live run, call 2
        "summary": "The Nov 4 spike to 58 Illegal Parking requests in Brooklyn CD3 (≈3.0× the "
        "usual ~19.4) coincided with 38 reports labeled “Posted Parking Sign Violation” out of "
        "58. There were no city‑permitted events in the district that day and no rain (0.0 mm) "
        "with a high of 22.9°C at Central Park.",
        "evidence": ["functions.series", "functions.breakdown", "functions.neighbours",
                     "functions.weather", "functions.events"],
        "wording_check": True,
    }  # fmt: skip
    five = [
        tool_call("series", {"days": 60}, "a"),
        tool_call("breakdown", {"by": "descriptor", "top": 10}, "b"),
        tool_call("neighbours", {}, "c"),
        tool_call("weather", {}, "d"),
        tool_call("events", {}, "e"),
    ]
    model = Scripted(
        [reply(*five), reply(tool_call("submit_finding", live, "s")), reply(submit(OK, [4]))]
    )
    f = go(model, db)
    want = "refs you can cite: 1 series, 2 breakdown, 3 neighbours, 4 weather, 5 events"
    assert want in shown(f, 2) and f.status == "investigated"


def test_a_refusal_on_the_last_call_is_an_unfinished_finding(db: Path) -> None:
    model = Scripted(
        [reply(tool_call("weather", {}, "w"))] + [reply(submit(OK, [9])) for _ in range(4)]
    )
    f = go(model, db)
    assert len(model.sent) == 5 and f.status == "unfinished"
    assert f.summary == "" and f.evidence == [] and len(f.trace) == 5 and str(f.cost) == "0.005"


def test_a_stopped_run_is_an_unfinished_finding_with_nothing_cited(db: Path) -> None:
    failures: list[str] = []

    def broken(*_: object) -> object:
        failures.append("x")
        raise TimeoutError("slow")

    model = Scripted([reply(tool_call("weather", {}, "w")), reply(submit(OK, [1]))])
    f = investigate.run(
        candidate(), db, model, weather_for=broken, events_for=events_for,  # type: ignore[arg-type]
        stop=lambda: bool(failures),
    )  # fmt: skip
    assert len(model.sent) == 1 and f.status == "unfinished"
    assert (
        f.summary == "" and f.evidence == [] and any("no further model calls" in n for n in f.notes)
    )


def test_the_first_message_states_the_call_budget(db: Path) -> None:
    model = Scripted([reply(tool_call("weather", {}, "w")), reply(submit(OK, [1]))])
    go(model, db)
    first = model.sent[0]["messages"][1]["content"]
    assert "You have 5 calls" in first and "separate reply" in first


@pytest.mark.parametrize("calls", [1, 2])
def test_a_budget_too_small_to_read_then_submit_is_said_and_unfinished(
    db: Path, calls: int
) -> None:
    model = Scripted([reply(submit(OK, [1])) for _ in range(calls)])
    f = go(model, db, max_calls=calls)
    assert "cannot read a result and then submit" in model.sent[0]["messages"][1]["content"]
    assert f.status == "unfinished" and f.summary == "" and f.evidence == []


def test_a_failed_model_call_after_a_refusal_keeps_the_cost(db: Path) -> None:
    from decimal import Decimal

    model = Scripted(
        [
            reply(tool_call("weather", {}, "w")),
            reply(submit(OK, [3])),
            CallFailed("x", Usage(0, 0, Decimal("0.008"))),
        ]
    )
    f = go(model, db)
    assert f.status == "unfinished" and f.summary == "" and str(f.cost) == "0.010"


def _garbage(name: str) -> dict:  # type: ignore[type-arg]
    return {"id": "g", "type": "function", "function": {"name": name, "arguments": "{not json"}}


@pytest.mark.parametrize(
    "kind", ["bad arguments", "garbled json", "query timeout", "source failure"]
)
def test_every_kind_of_failed_attempt_takes_a_number_and_is_never_citable(
    db: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    def broken(_: object) -> object:
        raise TimeoutError("slow")

    failing = {
        "bad arguments": tool_call("series", {"days": 500}, "a"),
        "garbled json": _garbage("series"),
        "query timeout": tool_call("neighbours", {}, "a"),
        "source failure": tool_call("weather", {}, "a"),
    }[kind]
    if kind == "query timeout":

        def slow(self: object, *a: object) -> object:
            raise ToolError("the query took longer than 2 seconds")

        monkeypatch.setattr(Toolbox, "_neighbours", slow)
    model = Scripted(
        [
            reply(failing, tool_call("breakdown", {"by": "descriptor", "top": 2}, "b")),
            reply(submit(OK, [1])),
            reply(submit(OK, [2])),
        ]
    )
    f = go(model, db, **({"weather_for": broken} if kind == "source failure" else {}))
    assert shown(f, 1, 0).startswith("ref 1: error: ") and shown(f, 1, 1).startswith("ref 2: ")
    refusal = shown(f, 2)
    assert "refs you can cite: 2 breakdown" in refusal
    assert failing["function"]["name"] not in refusal.split("cite:")[1]
    assert f.status == "investigated" and [e.tool for e in f.evidence] == ["breakdown"]


def test_a_failing_source_and_a_refused_submission_in_one_reply_end_the_run(db: Path) -> None:
    failures: list[str] = []

    def broken(_: object) -> object:
        failures.append("weather")
        raise TimeoutError("slow")

    model = Scripted(
        [
            reply(tool_call("weather", {}, "w"), submit(OK, [9])),
            reply(submit(OK, [1])),
        ]
    )
    f = investigate.run(
        candidate(), db, model, weather_for=broken, events_for=events_for,  # type: ignore[arg-type]
        stop=lambda: bool(failures),
    )  # fmt: skip
    assert len(model.sent) == 1 and len(model.script) == 1
    assert f.status == "unfinished" and f.summary == "" and f.evidence == []
    assert len(f.trace) == 1 and str(f.cost) == "0.001"
    assert shown(f, 1, 1).startswith("error: ")  # the submission was refused, not accepted
    assert any("no further model calls" in n for n in f.notes)


def test_stored_evidence_carries_its_ref_number_in_ref_order(db: Path) -> None:
    model = Scripted([reply(*three_tools()), reply(submit(OK, [3, 1]))])
    f = go(model, db)
    assert [(e.ref, e.tool) for e in f.evidence] == [(1, "series"), (3, "weather")]
