from typing import Any

from conftest import FAILING_DAY, Scripted, call, reply

from pothole.repair.agent import FORCE, repair
from pothole.repair.validate import Batch
from pothole.spec import ops
from pothole.spec.checks import CheckResult
from pothole.spec.model import PipelineSpec

RENAME = {"op": "map_field", "column": "agency", "source": "agency_acronym"}


def run(model: Scripted, spec: PipelineSpec, failing: Batch, checks: list[CheckResult],
        fields: dict[str, str], **kw: Any):  # fmt: skip
    return repair(spec, failing.rows, checks, fields, model, now=failing.now, **kw)  # type: ignore[arg-type]


def test_renamed_field_gets_a_patch_that_passes(spec, failing, failed_checks, fields):
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
                        "reason": "The source renamed agency to agency_acronym.",
                    },
                )  # fmt: skip
            ),
        ]
    )
    out = run(model, spec, failing, failed_checks, fields)
    assert out.action == "patch"
    assert [o.model_dump() for o in out.ops] == [RENAME]
    assert out.regression is not None and len(out.regression.rows) == 5
    assert {r["agency"] for r in out.regression.expected} != {None}
    assert str(out.cost) == "0.003" and len(out.trace) == 3
    assert FAILING_DAY not in str(model.sent[0]["messages"][0])
    assert "fail schema" in model.sent[0]["messages"][1]["content"]
    # the try_patch result the model saw has no failing check
    seen = model.sent[2]["messages"][-1]["content"]
    assert "fail " not in seen


def test_asking_to_edit_a_check_is_a_tool_error_and_counts(spec, failing, failed_checks, fields):
    bad = {"op": "set_check", "column": "latitude", "source": "x"}
    model = Scripted(
        [
            reply(call("try_patch", {"ops": [bad]})),
            reply(call("submit", {"action": "issue", "reason": "cannot edit checks"})),
        ]
    )
    out = run(model, spec, failing, failed_checks, fields)
    assert "error: ForbiddenEdit" in model.sent[1]["messages"][-1]["content"]
    assert out.action == "issue" and len(out.trace) == 2


def test_six_calls_without_submit_force_submit_and_end_in_an_issue(
    spec, failing, failed_checks, fields
):
    model = Scripted([reply(call("read_spec", {})) for _ in range(6)])
    out = run(model, spec, failing, failed_checks, fields)
    assert [s["tool_choice"] for s in model.sent] == [None] * 4 + [FORCE, FORCE]
    assert "2 calls left" in model.sent[4]["messages"][-1]["content"]
    assert out.action == "issue" and len(model.sent) == 6
    assert "no valid decision" in out.reason


def test_forced_submit_is_accepted(spec, failing, failed_checks, fields):
    script = [reply(call("read_spec", {})) for _ in range(4)]
    script.append(reply(call("submit", {"action": "no_change", "reason": "a real spike"})))
    out = run(Scripted(script), spec, failing, failed_checks, fields)
    assert out.action == "no_change" and out.ops == []


def test_invalid_submission_is_returned_as_an_error_then_can_be_fixed(
    spec, failing, failed_checks, fields
):
    nothing = {"action": "patch", "ops": [], "reason": "x"}
    good = {"action": "patch", "ops": [RENAME], "reason": "r"}
    model = Scripted([reply(call("submit", nothing)), reply(call("submit", good))])
    out = run(model, spec, failing, failed_checks, fields)
    assert model.sent[1]["messages"][-1]["content"].startswith("error:")
    assert out.action == "patch" and len(out.trace) == 2


def test_edits_are_limited_to_columns_with_a_failed_check_and_fields_the_batch_sends(
    spec, failing, failed_checks, fields
):
    args = {"action": "patch", "reason": "r"}
    other_column = {"op": "map_field", "column": "descriptor", "source": "location_type"}
    absent_field = {"op": "map_field", "column": "agency", "source": "nope"}
    model = Scripted(
        [
            reply(call("submit", {**args, "ops": [RENAME, other_column]})),
            reply(call("try_patch", {"ops": [absent_field]})),
            reply(call("submit", {"action": "issue", "reason": "unsure"})),
        ]
    )
    run(model, spec, failing, failed_checks, fields)
    assert "has no failed check" in model.sent[1]["messages"][-1]["content"]
    assert "does not send" in model.sent[2]["messages"][-1]["content"]


def test_a_patch_that_leaves_a_failure_is_refused(spec, failing, failed_checks, fields):
    # the batch also lacks latitude values: remapping agency alone fixes only the first failure
    rows = [{k: v for k, v in r.items() if k != "latitude"} for r in failing.rows]
    from pothole.spec import engine

    checks = engine.run(spec, rows, [], failing.now).checks
    args = {"action": "patch", "ops": [RENAME], "reason": "r"}
    model = Scripted(
        [reply(call("submit", args)), reply(call("submit", {"action": "issue", "reason": "x"}))]
    )
    repair(spec, rows, checks, fields, model, now=failing.now)  # type: ignore[arg-type]
    assert "still fails on the failing batch" in model.sent[1]["messages"][-1]["content"]


def test_rows_that_fail_under_the_patch_are_left_out_of_the_case(
    spec, failing, failed_checks, fields
):
    first = sorted(r["unique_key"] for r in failing.rows)[:4]
    rows = [dict(r, latitude="0") if r["unique_key"] in first else r for r in failing.rows]
    args = {"action": "patch", "ops": [RENAME], "reason": "r"}
    model = Scripted([reply(call("submit", args))])
    out = repair(spec, rows, failed_checks, fields, model, now=failing.now)  # type: ignore[arg-type]
    assert out.action == "patch" and out.regression is not None
    assert not set(first) & {r["unique_key"] for r in out.regression.rows}


def test_the_model_is_not_asked_for_regression_keys(spec, failing, failed_checks, fields):
    from pothole.repair import prompt

    submit = next(t for t in prompt.TOOLS if t["function"]["name"] == "submit")
    assert "regression_keys" not in submit["function"]["parameters"]["properties"]
    assert "regression_keys" not in prompt.SYSTEM_PROMPT


def test_the_code_chooses_the_same_five_rows_sorted_by_key(spec, failing, failed_checks, fields):
    args = {"action": "patch", "ops": [RENAME], "reason": "r"}
    cases = []
    for rows in (failing.rows, list(reversed(failing.rows))):
        model = Scripted([reply(call("submit", args))])
        out = repair(spec, rows, failed_checks, fields, model, now=failing.now)  # type: ignore[arg-type]
        assert out.regression is not None
        cases.append([r["unique_key"] for r in out.regression.rows])
    assert cases[0] == cases[1] and len(cases[0]) == 5 and cases[0] == sorted(cases[0])


def test_rows_that_send_the_edited_field_come_first(spec, failing):
    from pothole.repair.validate import choose_rows

    patched = ops.apply(spec, [ops.parse(RENAME)])
    ordered = sorted(r["unique_key"] for r in failing.rows)
    lacking = set(ordered[:3])
    rows = [
        r if r["unique_key"] not in lacking else {**r, "agency_acronym": ""} for r in failing.rows
    ]
    chosen = choose_rows(spec, patched, rows, ["agency"])
    assert not lacking & {r["unique_key"] for r in chosen}


def test_a_batch_too_small_for_a_case_is_refused(spec, failing):
    import pytest

    from pothole.repair.validate import ToolError, choose_rows

    patched = ops.apply(spec, [ops.parse(RENAME)])
    with pytest.raises(ToolError, match="at least 3"):
        choose_rows(spec, patched, failing.rows[:2], ["agency"])


def test_tools_show_rows_and_fields_but_nothing_else(spec, failing, failed_checks, fields):
    model = Scripted(
        [
            reply(
                call("sample_rows", {"column": "agency_acronym", "only_failing": False, "n": 99}),
                call("source_fields", {}, "c2"),
                call("sample_rows", {"column": "nope", "only_failing": True, "n": 3}, "c3"),
            ),
            reply(call("submit", {"action": "issue", "reason": "x"})),
        ]
    )
    run(model, spec, failing, failed_checks, fields)
    tool_msgs = [m["content"] for m in model.sent[1]["messages"] if m.get("role") == "tool"]
    assert tool_msgs[0].count('"key"') == 20
    assert "agency_acronym" in tool_msgs[1]
    assert tool_msgs[2].startswith("error:")


def test_a_model_that_answers_in_prose_is_nudged(spec, failing, failed_checks, fields):
    model = Scripted(
        [reply(content="I think so"), reply(call("submit", {"action": "issue", "reason": "x"}))]
    )
    out = run(model, spec, failing, failed_checks, fields)
    assert (
        out.action == "issue"
        and "Call one of the tools" in model.sent[1]["messages"][-1]["content"]
    )


def test_input_too_large_ends_in_an_issue_without_cost(spec, failing, failed_checks, fields):
    from pothole.llm.client import InputTooLarge

    out = run(Scripted([InputTooLarge("13000 > 12000")]), spec, failing, failed_checks, fields)
    assert out.action == "issue" and "13000" in out.reason and str(out.cost) == "0"


def test_a_failed_call_is_charged_and_ends_in_an_issue(spec, failing, failed_checks, fields):
    from decimal import Decimal

    from pothole.llm.client import CallFailed, Usage

    boom = CallFailed("server error 503", Usage(0, 0, Decimal("0.008"), charged_max=True))
    out = run(Scripted([reply(call("read_spec", {})), boom]), spec, failing, failed_checks, fields)
    assert out.action == "issue" and str(out.cost) == "0.009" and out.trace[-1]["error"]


def test_a_filtered_reply_ends_in_an_issue(spec, failing, failed_checks, fields):
    from decimal import Decimal

    from pothole.llm.client import CallResult, Usage

    filtered = CallResult({}, Usage(10, 0, Decimal("0.001")), "content_filter", 0.0)
    out = run(Scripted([filtered]), spec, failing, failed_checks, fields)
    assert out.action == "issue" and "content_filter" in out.reason


def test_two_submissions_in_one_reply_count_the_first_only(spec, failing, failed_checks, fields):
    model = Scripted(
        [
            reply(
                call("submit", {"action": "no_change", "reason": "first"}, "a"),
                call("submit", {"action": "issue", "reason": "second"}, "b"),
            )
        ]
    )
    out = run(model, spec, failing, failed_checks, fields)
    assert out.action == "no_change" and out.reason == "first"
    assert out.trace[0]["tool_results"][1]["content"] == "ignored: one submission per reply"


def test_old_tool_results_are_trimmed_to_fit_the_input_limit(spec, failing, failed_checks, fields):
    from pothole.budget.limits import CallLimits

    model = Scripted(
        [
            reply(call("read_spec", {})),
            reply(call("submit", {"action": "no_change", "reason": "x"})),
        ]
    )
    run(model, spec, failing, failed_checks, fields, limits=CallLimits(input_tokens=2400))
    assert any(
        "[trimmed]" in m["content"] for m in model.sent[1]["messages"] if m["role"] == "tool"
    )


def test_a_created_date_reformat_can_be_submitted(spec, failing, fields):
    from datetime import datetime

    from pothole.spec import engine

    def us(raw: str) -> str:
        return datetime.fromisoformat(raw).strftime("%m/%d/%Y %H:%M:%S")

    rows = [dict(r, created_date=us(r["created_date"])) for r in failing.rows]
    checks = engine.run(spec, rows, [], failing.now).checks
    fmt = {"op": "set_format", "column": "created_date", "format": "%m/%d/%Y %H:%M:%S"}
    sub = {"action": "patch", "ops": [RENAME, fmt], "reason": "r"}
    model = Scripted([reply(call("submit", sub))])
    out = repair(spec, rows, checks, fields, model, now=failing.now)  # type: ignore[arg-type]
    assert out.action == "patch" and out.regression is not None
