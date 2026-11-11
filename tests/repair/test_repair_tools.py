import json

from conftest import Scripted, call, reply

from pothole.repair.agent import repair


def test_source_fields_say_which_are_in_use_known_unused_or_new(
    spec, failing, failed_checks, fields
):
    model = Scripted(
        [
            reply(call("source_fields", {})),
            reply(call("submit", {"action": "issue", "reason": "x"})),
        ]
    )
    extra = {**fields, "agency_name": "text", "borough": "text"}
    repair(spec, failing.rows, failed_checks, extra, model, now=failing.now)  # type: ignore[arg-type]
    last = [m for m in model.sent[1]["messages"] if m.get("role") == "tool"][-1]
    seen = json.loads(last["content"].splitlines()[0])
    assert seen["agency_acronym"]["status"] == "new"
    assert seen["agency_name"] == {"type": "text", "status": "known_unused"}
    assert seen["borough"]["status"] == "in_use"


def test_formats_that_parse_reads_the_failing_batch(spec, failing, failed_checks, fields):
    rows = [{**r, "created_date": "11/09/2026 10:00:00 AM"} for r in failing.rows]
    model = Scripted(
        [
            reply(
                call("formats_that_parse", {"column": "created_date"}),
                call("formats_that_parse", {"column": "agency"}, "c2"),
                call("formats_that_parse", {"column": "nope"}, "c3"),
            ),
            reply(call("submit", {"action": "issue", "reason": "x"})),
        ]
    )
    repair(spec, rows, failed_checks, fields, model, now=failing.now)  # type: ignore[arg-type]
    tool = [m["content"] for m in model.sent[1]["messages"] if m.get("role") == "tool"]
    assert json.loads(tool[0])["formats"] == ["%m/%d/%Y %I:%M:%S %p"]
    assert tool[1].startswith("error:") and tool[2].startswith("error:")


def test_a_rename_to_a_known_unused_field_is_refused(spec, failing, failed_checks, fields):
    bad = {"op": "map_field", "column": "agency", "source": "agency_name"}
    rows = [{**r, "agency_name": "Full Agency Name"} for r in failing.rows]
    model = Scripted(
        [
            reply(call("try_patch", {"ops": [bad]})),
            reply(call("submit", {"action": "issue", "reason": "x"})),
        ]
    )
    repair(spec, rows, failed_checks, fields, model, now=failing.now)  # type: ignore[arg-type]
    assert "known field that holds other data" in model.sent[1]["messages"][-1]["content"]
