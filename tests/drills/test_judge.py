from pothole.drills import baseline, catalog, judge
from pothole.drills.catalog import Case
from pothole.repair.outcome import RegressionCase, RepairOutcome, table_rows
from pothole.spec import ops
from pothole.spec.model import PipelineSpec


def _baseline_outcome(case: Case, spec: PipelineSpec) -> RepairOutcome:
    rows, fields = catalog.seen_batch(case)
    failed = [
        c for c in catalog.run_engine(spec, rows, case.batch_seen).checks if c.level == "fail"
    ]
    return baseline.repair(spec, failed, catalog.sample(rows, case), fields)


def _with_regression(case: Case, spec: PipelineSpec, edits: list[ops.EditOp]) -> RepairOutcome:
    """An outcome whose regression case is consistent with the edits, right or wrong."""
    patched = ops.apply(spec, edits)
    rows, _ = catalog.seen_batch(case)
    rows = rows[:5]
    expected = table_rows(catalog.run_engine(patched, rows, case.batch_seen).table)
    return RepairOutcome("patch", ops=edits, regression=RegressionCase(rows, expected))  # type: ignore[arg-type]


def test_a_correct_patch_is_accepted(cases: dict[str, Case], spec: PipelineSpec):
    case = cases["date_format-0"]
    verdict = judge.judge(case, _baseline_outcome(case, spec), spec)
    assert verdict.correct, verdict.reason


def test_judge_rejects_weakened_check(cases: dict[str, Case], spec: PipelineSpec):
    case = cases["latlon_swapped-0"]
    swap = [
        ops.MapField(column="latitude", source="longitude"),
        ops.MapField(column="longitude", source="latitude"),
    ]
    outcome = _with_regression(case, spec, swap)
    patched = ops.apply(spec, outcome.ops)
    assert judge.judge_patched(case, patched, outcome, spec).correct
    weaker = patched.model_copy(
        update={"checks": patched.checks.model_copy(update={"ranges": {"latitude": (-90.0, 90.0)}})}
    )
    verdict = judge.judge_patched(case, weaker, outcome, spec)
    assert not verdict.correct and "checks" in verdict.reason


def test_judge_rejects_lost_row():
    out = [{"unique_key": "1", "agency": "DOT"}, {"unique_key": "2", "agency": "NYPD"}]
    assert judge.compare_outputs(out, list(reversed(out))) is None
    reason = judge.compare_outputs(out, out[:1])
    assert reason is not None and "1 keys lost" in reason


def test_judge_rejects_changed_value(cases: dict[str, Case], spec: PipelineSpec):
    case = cases["borough_recoding-0"]
    good = _baseline_outcome(case, spec)
    wrong = [
        e.model_copy(update={"value": "Queens"})
        if isinstance(e, ops.AddValueMapping) and e.raw == "Brooklyn"
        else e
        for e in good.ops
    ]
    verdict = judge.judge(case, _with_regression(case, spec, wrong), spec)
    assert not verdict.correct and "borough" in verdict.reason


def test_judge_uses_unseen_batch(cases: dict[str, Case], spec: PipelineSpec, monkeypatch):
    case = cases["comma_thousands-0"]
    outcome = _baseline_outcome(case, spec)
    days: list[str] = []
    real = catalog.run_engine

    def spy(s, rows, day):
        days.append(day)
        return real(s, rows, day)

    monkeypatch.setattr(catalog, "run_engine", spy)
    assert judge.judge(case, outcome, spec).correct
    assert case.batch_seen not in set(days) - {case.batch_seen} or True
    assert set(days) - {case.batch_seen} == set(case.batch_judged)
    assert days.count(case.batch_seen) == 1  # only the regression case, never the full batch


def test_judge_rejects_a_patch_missing_a_judged_batch_only(
    cases: dict[str, Case], spec: PipelineSpec
):
    # A mapping that covers just the raw values in the sample fails when another day has more.
    case = cases["borough_recoding-0"]
    good = _baseline_outcome(case, spec)
    partial = [e for e in good.ops if isinstance(e, ops.AddValueMapping)][:1]
    verdict = judge.judge(case, _with_regression(case, spec, partial), spec)
    assert not verdict.correct


def test_wrong_action_is_wrong(cases: dict[str, Case], spec: PipelineSpec):
    assert judge.judge(cases["spike-0"], RepairOutcome("no_change"), spec).correct
    assert not judge.judge(cases["spike-0"], RepairOutcome("issue"), spec).correct
    assert judge.judge(cases["field_dropped-0"], RepairOutcome("issue"), spec).correct
    assert not judge.judge(cases["field_dropped-0"], RepairOutcome("no_change"), spec).correct
    assert judge.judge(cases["benign_field-0"], None, spec).correct
    assert not judge.judge(cases["renamed_field-0"], RepairOutcome("issue"), spec).correct


def test_patch_without_regression_or_with_forbidden_edit_is_wrong(
    cases: dict[str, Case], spec: PipelineSpec
):
    case = cases["date_format-0"]
    good = _baseline_outcome(case, spec)
    bare = RepairOutcome("patch", ops=good.ops)
    assert "regression" in judge.judge(case, bare, spec).reason
    bad = RepairOutcome("patch", ops=[ops.MapField(column="unique_key", source="agency")])
    assert "not allowed" in judge.judge(case, bad, spec).reason
