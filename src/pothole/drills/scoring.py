"""Run a repairer over the catalog, have the judge score each case, and summarise a split."""

import json
import sys
from collections import defaultdict
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pothole.drills import baseline, catalog, faults, judge
from pothole.drills.catalog import Case
from pothole.repair.outcome import RepairOutcome
from pothole.spec.checks import CheckResult
from pothole.spec.model import PipelineSpec

Repairer = Callable[
    [PipelineSpec, list[CheckResult], list[dict[str, Any]], dict[str, str]], RepairOutcome
]


@dataclass(frozen=True)
class CaseResult:
    id: str
    family: str
    expected: str
    detected: bool
    action: str | None
    correct: bool
    reason: str


def run_case(case: Case, spec: PipelineSpec, repairer: Repairer) -> CaseResult:
    """One case. A repairer that raises, or an outcome the judge cannot read, is a wrong case."""
    rows, fields = catalog.seen_batch(case)
    checks = catalog.run_engine(spec, rows, case.batch_seen).checks
    found = faults.detected(catalog.fault_of(case), checks)
    outcome = None
    try:
        if case.expected_action != "nothing":  # a warning alone never calls the repairer
            failed = [c for c in checks if c.level == "fail"]
            outcome = repairer(spec, failed, rows, fields)
            if not isinstance(outcome, RepairOutcome):
                raise TypeError(f"the repairer returned {type(outcome).__name__}")
        verdict = judge.judge(case, outcome, spec)
    except Exception as err:  # any failure of a repairer is scored, never raised
        reason = f"crashed: {type(err).__name__}: {err}"
        return CaseResult(
            case.id, case.family, case.expected_action, found, "crashed", False, reason
        )
    action = outcome.action if outcome else None
    return CaseResult(
        case.id, case.family, case.expected_action, found, action, verdict.correct, verdict.reason
    )


def _rate(results: list[CaseResult], expected: str) -> dict[str, Any] | None:
    rows = [r for r in results if r.expected == expected]
    if not rows:
        return None
    ok = sum(r.correct for r in rows)
    return {"correct": ok, "of": len(rows), "rate": round(ok / len(rows), 4)}


def _detected(rs: list[CaseResult]) -> dict[str, Any]:
    found = sum(r.detected for r in rs)
    return {"detected": found, "of": len(rs), "rate": round(found / len(rs), 4) if rs else None}


def summarise(results: list[CaseResult], split: str, who: str) -> dict[str, Any]:
    faulted = [r for r in results if r.expected != "nothing"]
    benign = [r for r in results if r.expected == "nothing"]
    repairable = [r for r in results if r.expected == "patch"]
    gave_issue = sum(r.action == "issue" for r in repairable)
    families: dict[str, list[CaseResult]] = defaultdict(list)
    for r in results:
        families[r.family].append(r)
    return {
        "repairer": who,
        "split": split,
        "cases": len(results),
        "repair_success": _rate(results, "patch"),
        "issue_accuracy": _rate(results, "issue"),
        "no_change_accuracy": _rate(results, "no_change"),
        "repairable_answered_with_issue": {
            "count": gave_issue,
            "of": len(repairable),
            "rate": round(gave_issue / len(repairable), 4) if repairable else None,
        },
        "crashed": sum(r.action == "crashed" for r in results),
        "detection_coverage_fault_cases": _detected(faulted),
        "detection_coverage_benign_control": _detected(benign),
        "per_family": {
            f: {
                "expected": rs[0].expected,
                "cases": len(rs),
                "correct": sum(r.correct for r in rs),
                "detected": sum(r.detected for r in rs),
            }
            for f, rs in families.items()
        },
        "failures": [asdict(r) for r in results if not r.correct],
    }


def score(split: str, repairer: Repairer, who: str) -> dict[str, Any]:
    spec = catalog.load_spec()
    cases = [c for c in catalog.load() if c.split == split]
    return summarise([run_case(c, spec, repairer) for c in cases], split, who)


if __name__ == "__main__":
    split = sys.argv[1]
    out = Path(sys.argv[2]) / f"baseline-{split}" / "summary.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(score(split, baseline.repair, "baseline"), indent=2) + "\n")
