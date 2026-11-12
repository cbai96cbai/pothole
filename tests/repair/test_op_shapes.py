"""An edit whose `op` is not a string naming an operation is refused, never a crash."""

import json
from typing import Any

import pytest
from conftest import Scripted, call, reply

from pothole.repair.agent import repair
from pothole.spec import ops


@pytest.mark.parametrize(
    "raw", [{"op": []}, {"op": {}}, {"op": 5}, {"op": None}, {}, "map_field", [], None]
)
def test_parse_refuses_a_malformed_edit(raw: Any) -> None:
    with pytest.raises(ops.ForbiddenEdit):
        ops.parse(raw)


@pytest.mark.parametrize("tool", ["try_patch", "submit"])
def test_a_list_op_through_the_repair_loop_is_a_refusal_the_model_reads(
    spec, failing, failed_checks, fields, tool
):
    bad = [{"op": [], "column": "agency"}]
    args: dict[str, Any] = {"ops": bad}
    if tool == "submit":
        args = {"action": "patch", "reason": "r", "ops": bad}
    model = Scripted(
        [reply(call(tool, args)), reply(call("submit", {"action": "issue", "reason": "x"}))]
    )
    repair(spec, failing.rows, failed_checks, fields, model, now=failing.now)  # type: ignore[arg-type]
    seen = [m["content"] for m in model.sent[1]["messages"] if m.get("role") == "tool"]
    assert seen[0].startswith("error: ") and "unknown operation" in seen[0], json.dumps(seen)
