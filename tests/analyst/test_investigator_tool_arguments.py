"""A wrong argument from the model is a numbered tool error, never an exception."""

import json
from pathlib import Path
from typing import Any

import pytest
from investigator_kit import (
    Scripted,
    candidate,
    events_for,
    make_store,
    reply,
    tool_call,
    weather_for,
)

from pothole.analyst import investigate
from pothole.analyst.prompt import TOOLS

WRONG = {"string": [], "integer": "x", "boolean": "yes", "array": "x", "object": []}


def cases() -> list[Any]:
    out = []
    for t in TOOLS:
        fn = t["function"]
        for key, prop in fn["parameters"]["properties"].items():
            out.append(pytest.param(fn["name"], key, WRONG[prop["type"]], id=f"{fn['name']}.{key}"))
            if "enum" in prop:
                out.append(pytest.param(fn["name"], key, "zzz", id=f"{fn['name']}.{key}.enum"))
    return out


@pytest.mark.parametrize(("tool", "key", "wrong"), cases())
def test_a_wrong_argument_gives_an_error_result(
    tmp_path: Path, tool: str, key: str, wrong: Any
) -> None:
    db = make_store(tmp_path / "raw.duckdb")
    ok = {
        "days": 3,
        "by": "descriptor",
        "top": 3,
        "summary": "s",
        "evidence": [1],
        "wording_check": True,
    }
    args = {k: v for k, v in ok.items() if k in TOOLS_PROPS[tool]}
    args[key] = wrong
    model = Scripted([reply(tool_call(tool, args, "a")), reply(tool_call("weather", {}, "b"))] * 3)
    f = investigate.run(
        candidate(), db, model, weather_for=weather_for, events_for=events_for, max_calls=2
    )
    first = f.trace[0]["tool_results"][0]["content"]
    # evidence is refused through the refs: with none made yet it says so
    named = f"{key} must" in first or "no refs you can cite yet" in first
    assert "error: " in first and named
    assert f.summary == "" and f.evidence == []


TOOLS_PROPS = {t["function"]["name"]: set(t["function"]["parameters"]["properties"]) for t in TOOLS}


def test_the_reported_case_breakdown_with_a_list_for_by(tmp_path: Path) -> None:
    db = make_store(tmp_path / "raw.duckdb")
    args = json.dumps({"by": [], "top": 3})
    call = {"id": "a", "type": "function", "function": {"name": "breakdown", "arguments": args}}
    model = Scripted([reply(call)] + [reply(tool_call("weather", {}, "b"))] * 4)
    f = investigate.run(candidate(), db, model, weather_for=weather_for, events_for=events_for)
    assert f.trace[0]["tool_results"][0]["content"].startswith("ref 1: error: by must be")
