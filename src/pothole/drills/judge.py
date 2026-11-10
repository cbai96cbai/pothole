"""The independent judge. It applies a submitted patch to the original spec and runs the engine
on three recorded days the repairer never saw, against each day's clean reference output."""

import json
from dataclasses import dataclass
from typing import Any

from pothole.drills import catalog
from pothole.drills.catalog import Case
from pothole.drills.faults import inject
from pothole.repair.outcome import RepairOutcome, table_rows
from pothole.spec import ops
from pothole.spec.model import PipelineSpec

Rows = list[dict[str, Any]]


@dataclass(frozen=True)
class Verdict:
    correct: bool
    reason: str


def _ordered(rows: Rows) -> Rows:
    return sorted(rows, key=lambda r: (r["unique_key"] or "", json.dumps(r, sort_keys=True)))


def compare_outputs(expected: Rows, got: Rows) -> str | None:
    """None when the outputs are identical after sorting by key, otherwise the first difference."""
    want, have = _ordered(expected), _ordered(got)
    want_keys = [r["unique_key"] for r in want]
    have_keys = [r["unique_key"] for r in have]
    if want_keys != have_keys:
        lost = len(set(want_keys) - set(have_keys))
        return f"rows differ: {len(want)} expected, {len(have)} produced, {lost} keys lost"
    for a, b in zip(want, have, strict=True):
        for col, value in a.items():
            if b.get(col) != value:
                return f"row {a['unique_key']}: {col} is {b.get(col)!r}, expected {value!r}"
    return None


def judge(case: Case, outcome: RepairOutcome | None, spec: PipelineSpec) -> Verdict:
    """outcome is None for the benign control, where nothing is run and nothing is scored."""
    if case.expected_action == "nothing":
        if outcome is None or outcome.action == "no_change":
            return Verdict(True, "nothing to repair")
        return Verdict(False, f"{outcome.action} on a benign change")
    if outcome is None:
        return Verdict(False, "no outcome")
    if case.expected_action != "patch":
        ok = outcome.action == case.expected_action
        return Verdict(ok, f"expected {case.expected_action}, got {outcome.action}")
    if outcome.action != "patch" or not outcome.ops:
        return Verdict(False, f"expected a patch, got {outcome.action}")
    try:
        patched = ops.apply(spec, outcome.ops)
    except ops.ForbiddenEdit as err:
        return Verdict(False, f"the edits are not allowed: {err}")
    return judge_patched(case, patched, outcome, spec)


def judge_patched(
    case: Case, patched: PipelineSpec, outcome: RepairOutcome, spec: PipelineSpec
) -> Verdict:
    """The patched spec against the original: same checks, the regression case, then three days."""
    if patched.checks.model_dump_json() != spec.checks.model_dump_json():
        return Verdict(False, "the checks differ from the original spec")
    if patched.source != spec.source:
        return Verdict(False, "the source differs from the original spec")
    if outcome.regression is None:
        return Verdict(False, "no regression case")
    rows: Any = outcome.regression.rows
    got = table_rows(catalog.run_engine(patched, rows, case.batch_seen).table)
    if compare_outputs(outcome.regression.expected, got):
        return Verdict(False, "the regression case does not pass")
    fault = catalog.fault_of(case)
    for day in case.batch_judged:
        clean = catalog.clean_batch(day)
        want = table_rows(catalog.run_engine(spec, clean, day).table)
        have = table_rows(catalog.run_engine(patched, inject(clean, fault)[0], day).table)
        diff = compare_outputs(want, have)
        if diff:
            return Verdict(False, f"{day}: {diff}")
    return Verdict(True, f"output equals the clean reference on {len(case.batch_judged)} days")
