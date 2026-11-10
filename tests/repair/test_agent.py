from typing import Any

from conftest import FAILING_DAY, Scripted, call, reply

from pothole.repair.agent import FORCE, repair
from pothole.repair.validate import Batch
from pothole.spec.checks import CheckResult
from pothole.spec.model import PipelineSpec

RENAME = {"op": "map_field", "column": "agency", "source": "agency_name"}


def keys(failing: Batch, n: int = 4) -> list[str]:
    return [r["unique_key"] for r in failing.rows[:n]]


def run(model: Scripted, spec: PipelineSpec, failing: Batch, checks: list[CheckResult],
        fields: dict[str, str], healthy: list[Batch], **kw: Any):  # fmt: skip
    return repair(spec, failing.rows, checks, fields, model, now=failing.now, healthy=healthy, **kw)  # type: ignore[arg-type]


def test_renamed_field_gets_a_patch_that_passes(spec, failing, failed_checks, fields, healthy):
    model = Scripted(
        [
            reply(call("read_check_results", {})),
            reply(call("try_patch", {"ops": [RENAME]})),
            reply(
                call(
                    "submit",
                    {
                        "action": "patch",
                        "ops": [RENAME],
                        "regression_keys": keys(failing),
                        "reason": "The source renamed agency to agency_name.",
                    },
                )  # fmt: skip
            ),
        ]
    )
    out = run(model, spec, failing, failed_checks, fields, healthy)
    assert out.action == "patch"
    assert [o.model_dump() for o in out.ops] == [RENAME]
    assert out.regression is not None and len(out.regression.rows) == 4
    assert {r["agency"] for r in out.regression.expected} != {None}
    assert str(out.cost) == "0.003" and len(out.trace) == 3
    assert FAILING_DAY not in str(model.sent[0]["messages"][0])
    assert "fail schema" in model.sent[0]["messages"][1]["content"]
    # the try_patch result the model saw has no failing check
    seen = model.sent[2]["messages"][-1]["content"]
    assert "fail " not in seen


def test_asking_to_edit_a_check_is_a_tool_error_and_counts(
    spec, failing, failed_checks, fields, healthy
):
    bad = {"op": "set_check", "column": "latitude", "source": "x"}
    model = Scripted(
        [
            reply(call("try_patch", {"ops": [bad]})),
            reply(call("submit", {"action": "issue", "reason": "cannot edit checks"})),
        ]
    )
    out = run(model, spec, failing, failed_checks, fields, healthy)
    assert "error: ForbiddenEdit" in model.sent[1]["messages"][-1]["content"]
    assert out.action == "issue" and len(out.trace) == 2


def test_six_calls_without_submit_force_submit_and_end_in_an_issue(
    spec, failing, failed_checks, fields, healthy
):
    model = Scripted([reply(call("read_spec", {})) for _ in range(6)])
    out = run(model, spec, failing, failed_checks, fields, healthy)
    assert [s["tool_choice"] for s in model.sent] == [None] * 4 + [FORCE, FORCE]
    assert "2 calls left" in model.sent[4]["messages"][-1]["content"]
    assert out.action == "issue" and len(model.sent) == 6
    assert "no valid decision" in out.reason


def test_forced_submit_is_accepted(spec, failing, failed_checks, fields, healthy):
    script = [reply(call("read_spec", {})) for _ in range(4)]
    script.append(reply(call("submit", {"action": "no_change", "reason": "a real spike"})))
    out = run(Scripted(script), spec, failing, failed_checks, fields, healthy)
    assert out.action == "no_change" and out.ops == []


def test_invalid_submission_is_returned_as_an_error_then_can_be_fixed(
    spec, failing, failed_checks, fields, healthy
):
    nothing = {"action": "patch", "ops": [], "regression_keys": [], "reason": "x"}
    good = {"action": "patch", "ops": [RENAME], "regression_keys": keys(failing), "reason": "r"}
    model = Scripted([reply(call("submit", nothing)), reply(call("submit", good))])
    out = run(model, spec, failing, failed_checks, fields, healthy)
    assert model.sent[1]["messages"][-1]["content"].startswith("error:")
    assert out.action == "patch" and len(out.trace) == 2


def test_patch_that_still_fails_or_breaks_healthy_days_is_refused(
    spec, failing, failed_checks, fields, healthy
):
    wrong_field = {"op": "map_field", "column": "agency", "source": "nope"}
    breaks_old = [
        RENAME,
        {"op": "map_field", "column": "x_coordinate_state_plane", "source": "agency"},
    ]
    args = {"action": "patch", "regression_keys": keys(failing), "reason": "r"}
    model = Scripted(
        [
            reply(call("submit", {**args, "ops": [wrong_field]})),
            reply(call("submit", {**args, "ops": breaks_old})),
            reply(call("submit", {"action": "issue", "reason": "unsure"})),
        ]
    )
    run(model, spec, failing, failed_checks, fields, healthy)
    assert "still fails on the failing batch" in model.sent[1]["messages"][-1]["content"]
    assert "fails on healthy earlier batches" in model.sent[2]["messages"][-1]["content"]


def test_regression_keys_must_be_real_and_the_right_count(
    spec, failing, failed_checks, fields, healthy
):
    args = {"action": "patch", "ops": [RENAME], "reason": "r"}
    model = Scripted(
        [
            reply(call("submit", {**args, "regression_keys": keys(failing, 2)})),
            reply(call("submit", {**args, "regression_keys": ["a", "b", "c"]})),
            reply(call("submit", {"action": "issue", "reason": "x"})),
        ]
    )
    run(model, spec, failing, failed_checks, fields, healthy)
    assert "3 to 10" in model.sent[1]["messages"][-1]["content"]
    assert "not in the failing batch" in model.sent[2]["messages"][-1]["content"]


def test_tools_show_rows_and_fields_but_nothing_else(spec, failing, failed_checks, fields, healthy):
    model = Scripted(
        [
            reply(
                call("sample_rows", {"column": "agency_name", "only_failing": False, "n": 99}),
                call("source_fields", {}, "c2"),
                call("sample_rows", {"column": "nope", "only_failing": True, "n": 3}, "c3"),
            ),
            reply(call("submit", {"action": "issue", "reason": "x"})),
        ]
    )
    run(model, spec, failing, failed_checks, fields, healthy)
    tool_msgs = [m["content"] for m in model.sent[1]["messages"] if m.get("role") == "tool"]
    assert tool_msgs[0].count('"key"') == 20
    assert "agency_name" in tool_msgs[1]
    assert tool_msgs[2].startswith("error:")


def test_a_model_that_answers_in_prose_is_nudged(spec, failing, failed_checks, fields, healthy):
    model = Scripted(
        [reply(content="I think so"), reply(call("submit", {"action": "issue", "reason": "x"}))]
    )
    out = run(model, spec, failing, failed_checks, fields, healthy)
    assert (
        out.action == "issue"
        and "Call one of the tools" in model.sent[1]["messages"][-1]["content"]
    )
