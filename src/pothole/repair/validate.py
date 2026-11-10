"""What a proposed patch must satisfy before it is accepted, and the shared batch helpers."""

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from pothole.repair.outcome import RegressionCase, table_rows
from pothole.socrata import batches
from pothole.spec import engine
from pothole.spec.checks import CheckResult
from pothole.spec.model import PipelineSpec

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


def failing_columns(spec: PipelineSpec, results: list[CheckResult]) -> set[str]:
    """The columns a failed check names: the only ones an edit may touch."""
    by_source = {c.source: c.name for c in spec.columns}
    cols: set[str] = set()
    for res in failed(results):
        kind, _, col = res.name.partition(":")
        if col and kind in ("required", "cast", "values", "range", "null_rate"):
            cols.add(col)
        elif res.name == "schema":
            missing = re.split(r",\s*", res.detail.rsplit(": ", 1)[-1])
            cols |= {by_source[m] for m in missing if m in by_source}
    return cols


def case_day(spec: PipelineSpec, rows: list[dict[str, str]]) -> date:
    """The day the case's rows were created: the latest `created_date` among them."""
    source = spec.column("created_date").source
    days = [r[source][:10] for r in rows if r.get(source)]
    try:
        return max(date.fromisoformat(d) for d in days)
    except ValueError as exc:
        raise ToolError("the chosen rows have no readable created_date") from exc


def case_now(day: date) -> datetime:
    return datetime(day.year, day.month, day.day) + timedelta(days=1, hours=30)


def build_case(
    original: PipelineSpec,
    patched: PipelineSpec,
    rows: list[dict[str, str]],
    keys: list[str],
) -> RegressionCase:
    if len(set(keys)) != len(keys) or not MIN_CASE <= len(keys) <= MAX_CASE:
        raise ToolError(f"regression_keys must be {MIN_CASE} to {MAX_CASE} different keys")
    key_field = original.column(original.source.key).source
    by_key = {r.get(key_field, ""): r for r in rows}
    unknown = [k for k in keys if k not in by_key]
    if unknown:
        raise ToolError(f"regression_keys not in the failing batch: {unknown[:5]}")
    subset = [by_key[k] for k in keys]
    now = case_now(case_day(patched, subset))
    still = failed(engine.run(patched, subset, [], now).checks)
    if still:
        raise ToolError(
            "these rows fail checks under the patched spec; pick others: "
            + "; ".join(f"{r.name}: {r.detail}" for r in still[:3])
        )
    expected = table_rows(engine.run(patched, subset, [], now).table)
    before = table_rows(engine.run(original, subset, [], now).table)
    if expected == before:
        raise ToolError(
            "these rows come out the same with and without the patch; pick rows it fixes"
        )
    case = RegressionCase(subset, expected)
    if not reproduces(patched, case):
        raise ToolError("the regression case does not reproduce with the patched spec")
    return case


def reproduces(spec: PipelineSpec, case: RegressionCase) -> bool:
    got: Any = table_rows(
        engine.run(spec, case.rows, [], case_now(case_day(spec, case.rows))).table
    )
    return bool(json.loads(json.dumps(got)) == json.loads(json.dumps(case.expected)))
