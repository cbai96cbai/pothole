"""What a proposed patch must satisfy before it is accepted, and the shared batch helpers."""

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from pothole.repair.outcome import Op, RegressionCase, table_rows
from pothole.socrata import batches
from pothole.spec import engine
from pothole.spec.checks import CheckResult
from pothole.spec.model import PipelineSpec
from pothole.spec.ops import MapField, SetFormat

DEFAULT_HEALTHY = Path(__file__).parents[3] / "fixtures" / "batches"
MIN_CASE, MAX_CASE = 3, 10


class ToolError(Exception):
    """A tool call that cannot be honoured; its message goes back to the model."""


@dataclass(frozen=True)
class Batch:
    name: str
    rows: list[dict[str, str]]
    now: datetime


def batch_now(path: Path) -> datetime:
    """The batch date's end plus 30 hours, New York local time."""
    day = datetime.strptime(path.name.split(".")[0], "%Y-%m-%d")
    return day + timedelta(days=1, hours=30)


def load_batches(directory: Path) -> list[Batch]:
    found = sorted(directory.glob("*.jsonl.gz"))
    if not found:
        raise FileNotFoundError(f"no *.jsonl.gz batches in {directory}")
    return [Batch(p.name, batches.load(p), batch_now(p)) for p in found]


def failed(results: list[CheckResult]) -> list[CheckResult]:
    return [r for r in results if r.level == "fail"]


def _tolerated(op_list: list[Op], res: CheckResult) -> bool:
    """Older data cannot satisfy an edit that follows a change in the source: a remapped column's
    source field is absent from it, and a new format does not read old values. Those checks
    cannot pass on the healthy fixtures, and that is not a defect of the patch."""
    moved = {o.column: o.source for o in op_list if isinstance(o, MapField)}
    reformatted = {o.column for o in op_list if isinstance(o, SetFormat)}
    kind, _, col = res.name.partition(":")
    if kind in ("required", "null_rate") and col in moved:
        return True
    if kind in ("cast", "null_rate") and col in reformatted:
        return True
    if res.name == "freshness" and "created_date" in reformatted | set(moved):
        return True
    if res.name == "schema":
        missing = set(re.split(r",\s*", res.detail.rsplit(": ", 1)[-1]))
        return missing <= set(moved.values())
    return False


def check_healthy(
    patched: PipelineSpec, op_list: list[Op], healthy: list[Batch], history: list[int] | None = None
) -> list[str]:
    problems: list[str] = []
    for b in healthy:
        for res in failed(engine.run(patched, b.rows, history or [], b.now).checks):
            if not _tolerated(op_list, res):
                problems.append(f"{b.name}: {res.name}: {res.detail}")
    return problems


def build_case(
    original: PipelineSpec,
    patched: PipelineSpec,
    rows: list[dict[str, str]],
    keys: list[str],
    now: datetime | None,
) -> RegressionCase:
    if len(set(keys)) != len(keys) or not MIN_CASE <= len(keys) <= MAX_CASE:
        raise ToolError(f"regression_keys must be {MIN_CASE} to {MAX_CASE} different keys")
    key_field = original.column(original.source.key).source
    by_key = {r.get(key_field, ""): r for r in rows}
    unknown = [k for k in keys if k not in by_key]
    if unknown:
        raise ToolError(f"regression_keys not in the failing batch: {unknown[:5]}")
    subset = [by_key[k] for k in keys]
    expected = table_rows(engine.run(patched, subset, [], now).table)
    before = table_rows(engine.run(original, subset, [], now).table)
    if expected == before:
        raise ToolError(
            "these rows come out the same with and without the patch; pick rows it fixes"
        )
    case = RegressionCase(subset, expected)
    if not reproduces(patched, case, now):
        raise ToolError("the regression case does not reproduce with the patched spec")
    return case


def reproduces(spec: PipelineSpec, case: RegressionCase, now: datetime | None = None) -> bool:
    got: Any = table_rows(engine.run(spec, case.rows, [], now).table)
    return bool(json.loads(json.dumps(got)) == json.loads(json.dumps(case.expected)))
