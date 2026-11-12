"""A wrong argument from the model is an error result for the repair agent, never an exception."""

from typing import Any

import pytest
from conftest import Scripted, call, reply

from pothole.repair.agent import repair
from pothole.repair.prompt import TOOLS

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
    spec, failing, failed_checks, fields, tool, key, wrong
):
    ok = {"column": "agency", "n": 3, "only_failing": False, "action": "issue", "reason": "r"}
    props = next(
        t["function"]["parameters"]["properties"] for t in TOOLS if t["function"]["name"] == tool
    )
    args = {k: v for k, v in ok.items() if k in props}
    args[key] = wrong
    model = Scripted(
        [reply(call(tool, args)), reply(call("submit", {"action": "issue", "reason": "x"}))]
    )
    repair(spec, failing.rows, failed_checks, fields, model, now=failing.now)  # type: ignore[arg-type]
    results = [m["content"] for m in model.sent[1]["messages"] if m.get("role") == "tool"]
    assert results[0].startswith("error: ") and f"{key} must" in results[0]
