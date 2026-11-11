from typing import Any

from pothole.drills import baseline, catalog, scoring
from pothole.drills.catalog import Case
from pothole.repair.outcome import RegressionCase, RepairOutcome
from pothole.spec import ops
from pothole.spec.model import PipelineSpec


def _raises(*_: Any) -> RepairOutcome:
    raise RuntimeError("model timed out")


def _malformed_patch(*_: Any) -> RepairOutcome:
    """Valid edits, but the regression rows are not rows: the judge raises while reading them."""
    bad: Any = RegressionCase(rows=["x", "y", "z"], expected=[])  # type: ignore[arg-type]
    edit = ops.SetFormat(column="created_date", format="%Y-%m-%dT%H:%M:%S")
    return RepairOutcome("patch", ops=[edit], regression=bad)


def _not_an_outcome(*_: Any) -> Any:
    return {"action": "patch"}


def test_a_raising_repairer_is_a_wrong_case(cases: dict[str, Case], spec: PipelineSpec):
    r = scoring.run_case(cases["date_format-0"], spec, _raises)
    assert not r.correct and r.action == "crashed" and "model timed out" in r.reason


def test_malformed_outcomes_are_wrong_cases(cases: dict[str, Case], spec: PipelineSpec):
    for repairer in (_malformed_patch, _not_an_outcome):
        r = scoring.run_case(cases["date_format-0"], spec, repairer)
        assert not r.correct and r.action == "crashed", repairer


def test_scoring_never_aborts_and_counts_every_case(spec: PipelineSpec):
    cs = [c for c in catalog.load() if c.split == "test" and c.variant < 2]
    results = [scoring.run_case(c, spec, _raises) for c in cs]
    s = scoring.summarise(results, "test", "raiser")
    assert s["cases"] == len(cs)
    benign = sum(c.family == "benign_field" for c in cs)
    assert s["crashed"] == len(cs) - benign  # a benign control never calls the repairer
    assert s["repair_success"]["correct"] == 0


def test_a_benign_control_does_not_call_the_repairer(cases: dict[str, Case], spec: PipelineSpec):
    calls: list[int] = []

    def spy(*_: Any) -> RepairOutcome:
        calls.append(1)
        return RepairOutcome("issue")

    r = scoring.run_case(cases["benign_field-0"], spec, spy)
    assert calls == [] and r.correct and r.detected and r.action is None


def test_summary_counts(cases: dict[str, Case], spec: PipelineSpec):
    ids = ["date_format-0", "renamed_field-0", "field_dropped-0", "spike-0", "benign_field-0"]
    results = [scoring.run_case(cases[i], spec, baseline.repair) for i in ids]
    s = scoring.summarise(results, "dev", "baseline")
    assert s["repair_success"] == {"correct": 1, "of": 2, "rate": 0.5}
    assert s["repairable_answered_with_issue"] == {"count": 1, "of": 2, "rate": 0.5}
    assert s["issue_accuracy"]["correct"] == 1 and s["no_change_accuracy"]["correct"] == 1
    assert s["detection_coverage_fault_cases"] == {"detected": 4, "of": 4, "rate": 1.0}
    assert s["detection_coverage_benign_control"] == {"detected": 1, "of": 1, "rate": 1.0}
    assert s["per_family"]["renamed_field"] == {
        "expected": "patch",
        "cases": 1,
        "correct": 0,
        "detected": 1,
    }
    assert [f["id"] for f in s["failures"]] == ["renamed_field-0"]


def test_a_repairer_gets_the_batch_its_checks_were_computed_on(spec: PipelineSpec):
    """Its own re-check, with the same history, must not trip the volume check."""
    seen: list[Any] = []

    def spy(_spec: Any, failed: Any, rows: Any, fields: Any) -> RepairOutcome:
        seen.append(rows)
        return RepairOutcome("issue")

    for case in (c for c in catalog.load() if c.split == "dev" and c.expected_action == "patch"):
        seen.clear()
        scoring.run_case(case, spec, spy)
        full, _ = catalog.seen_batch(case)
        assert seen and len(seen[0]) == len(full), case.id
        again = catalog.run_engine(spec, seen[0], case.batch_seen).checks
        volume = next(c for c in again if c.name == "volume")
        assert volume.level != "fail", (case.id, volume.detail)
