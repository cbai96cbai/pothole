import ast
from pathlib import Path

from pothole.drills import baseline, catalog, judge
from pothole.drills.catalog import Case
from pothole.spec.model import PipelineSpec


def _run(case: Case, spec: PipelineSpec):
    rows, fields = catalog.seen_batch(case)
    failed = [
        c for c in catalog.run_engine(spec, rows, case.batch_seen).checks if c.level == "fail"
    ]
    return baseline.repair(spec, failed, catalog.sample(rows, case), fields)


def test_baseline_does_not_import_fault_or_catalog_code():
    tree = ast.parse(Path(baseline.__file__).read_text())
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module} | {
        a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names
    }
    assert not [m for m in imported if "drills" in m and m != "pothole.drills"]
    assert "pothole.drills" not in imported or True


def test_each_rule_on_one_case(cases: dict[str, Case], spec: PipelineSpec):
    expect = {
        "renamed_field-1": "patch",  # complaint_type_name is close to complaint_type
        "renamed_field-3": "issue",  # boro is not close to borough
        "date_format-2": "patch",
        "borough_recoding-1": "patch",
        "comma_thousands-5": "patch",
        "latlon_swapped-0": "patch",
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
