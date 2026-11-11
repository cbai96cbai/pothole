import ast
from pathlib import Path

from pothole.drills import baseline, catalog, judge
from pothole.drills.catalog import Case
from pothole.spec import ops, scope
from pothole.spec.model import PipelineSpec


def _run(case: Case, spec: PipelineSpec):
    rows, fields = catalog.seen_batch(case)
    failed = [
        c for c in catalog.run_engine(spec, rows, case.batch_seen).checks if c.level == "fail"
    ]
    return baseline.repair(spec, failed, rows, fields)


def test_baseline_does_not_import_fault_or_catalog_code():
    tree = ast.parse(Path(baseline.__file__).read_text())
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module} | {
        a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names
    }
    assert not [m for m in imported if "drills" in m and m != "pothole.drills"]


def test_each_rule_on_one_case(cases: dict[str, Case], spec: PipelineSpec):
    expect = {
        "renamed_field-1": "patch",  # complaint_type_name is close to complaint_type
        "renamed_field-3": "issue",  # boro is not close to borough
        "date_format-2": "patch",
        "borough_recoding-0": "patch",  # title case equals the listed value ignoring case
        "borough_recoding-1": "patch",
        "borough_recoding-5": "patch",  # trailing space
        "borough_recoding-2": "issue",  # two-letter codes are not equal ignoring case
        "borough_recoding-4": "issue",
        "comma_thousands-5": "patch",
        "latlon_swapped-0": "issue",  # no rule for out-of-range values
        "field_dropped-5": "issue",
        "null_flood-0": "issue",
        "partial_load-1": "issue",
        "source_empty-0": "issue",
        "spike-0": "no_change",
    }
    for cid, action in expect.items():
        outcome = _run(cases[cid], spec)
        assert outcome.action == action, cid
        if action == "patch":
            assert judge.judge(cases[cid], outcome, spec).correct, cid


def test_edits_touch_only_failed_columns_and_fields_the_batch_sends(
    cases: dict[str, Case], spec: PipelineSpec
):
    for cid in [
        "renamed_field-4",
        "renamed_field-1",
        "date_format-0",
        "date_format-4",
        "comma_thousands-4",
    ]:
        case = cases[cid]
        rows, fields = catalog.seen_batch(case)
        failed = [
            c for c in catalog.run_engine(spec, rows, case.batch_seen).checks if c.level == "fail"
        ]
        outcome = baseline.repair(spec, failed, rows, fields)
        assert outcome.action == "patch", cid
        assert {o.column for o in outcome.ops} <= scope.failing_columns(spec, failed)
        assert all(o.source in fields for o in outcome.ops if isinstance(o, ops.MapField))


def test_a_rename_to_a_field_the_batch_does_not_send_is_an_issue(
    cases: dict[str, Case], spec: PipelineSpec
):
    case = cases["renamed_field-1"]
    rows, fields = catalog.seen_batch(case)
    failed = [
        c for c in catalog.run_engine(spec, rows, case.batch_seen).checks if c.level == "fail"
    ]
    fields = {k: v for k, v in fields.items() if k != "complaint_type_name"}
    assert baseline.repair(spec, failed, rows, fields).action == "issue"
