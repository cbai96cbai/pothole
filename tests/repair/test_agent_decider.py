"""Who decided: an issue the code files because the model gave no valid decision is `code`; an
issue the model submitted is `agent`."""

from decimal import Decimal

from conftest import Scripted, call, reply

from pothole.budget.limits import CallLimits
from pothole.llm.client import CallFailed, CallResult, InputTooLarge, Usage
from pothole.repair.agent import repair


def run(model, spec, failing, checks, fields, **kw):
    return repair(spec, failing.rows, checks, fields, model, now=failing.now, **kw)


def test_an_issue_the_model_submitted_is_the_agents(spec, failing, failed_checks, fields):
    model = Scripted([reply(call("submit", {"action": "issue", "reason": "ask a person"}))])
    out = run(model, spec, failing, failed_checks, fields)
    assert out.action == "issue" and out.decided_by == "agent"


def test_a_patch_the_model_submitted_is_the_agents(spec, failing, failed_checks, fields):
    rename = {"op": "map_field", "column": "agency", "source": "agency_acronym"}
    model = Scripted([reply(call("submit", {"action": "patch", "ops": [rename], "reason": "r"}))])
    assert run(model, spec, failing, failed_checks, fields).decided_by == "agent"


def test_no_valid_decision_in_the_calls_is_the_codes(spec, failing, failed_checks, fields):
    model = Scripted([reply(call("read_spec", {})) for _ in range(6)])
    out = run(model, spec, failing, failed_checks, fields)
    assert out.action == "issue" and out.decided_by == "code"


def test_a_conversation_that_cannot_fit_is_the_codes(spec, failing, failed_checks, fields):
    out = run(Scripted([]), spec, failing, failed_checks, fields, limits=CallLimits(input_tokens=1))
    assert "no longer fits" in out.reason and out.decided_by == "code"


def test_an_input_too_large_is_the_codes(spec, failing, failed_checks, fields):
    out = run(Scripted([InputTooLarge("13000 > 12000")]), spec, failing, failed_checks, fields)
    assert out.decided_by == "code"


def test_a_failed_call_is_the_codes(spec, failing, failed_checks, fields):
    boom = CallFailed("server error 503", Usage(0, 0, Decimal("0.008"), charged_max=True))
    assert run(Scripted([boom]), spec, failing, failed_checks, fields).decided_by == "code"


def test_a_filtered_reply_is_the_codes(spec, failing, failed_checks, fields):
    filtered = CallResult({}, Usage(10, 0, Decimal("0.001")), "content_filter", 0.0)
    assert run(Scripted([filtered]), spec, failing, failed_checks, fields).decided_by == "code"
