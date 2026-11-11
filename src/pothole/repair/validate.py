"""What a proposed patch must satisfy before it is accepted, and the shared batch helpers."""

import json
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from pothole.repair.outcome import RegressionCase, table_rows
from pothole.socrata import batches
from pothole.spec import engine
from pothole.spec.checks import CheckResult
from pothole.spec.model import PipelineSpec

MIN_CASE, CASE_SIZE = 3, 5


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


def case_day(spec: PipelineSpec, rows: list[dict[str, str]]) -> date:
    """The day the case's rows were created: the latest `created_date` as the spec reads it."""
    created = engine.run(spec, rows, [], datetime(2100, 1, 1)).table.column("created_date")
    newest = max((v for v in created.to_pylist() if v is not None), default=None)
    if newest is None:
        raise ToolError("the chosen rows have no readable created_date")
    return newest.date()  # type: ignore[no-any-return]


def case_now(day: date) -> datetime:
    return datetime(day.year, day.month, day.day) + timedelta(days=1, hours=30)


def _changed_rows(
    original: PipelineSpec, patched: PipelineSpec, rows: list[dict[str, str]]
) -> list[bool]:
    """Per input row, by position: does its output differ between the original and patched spec?
    Position, not key, so a repeated key cannot hide or borrow another row's change."""
    now = datetime(2100, 1, 1)
    before = table_rows(engine.run(original, rows, [], now).table)
    after = table_rows(engine.run(patched, rows, [], now).table)
    return [b != a for b, a in zip(before, after, strict=True)]


UNREADABLE = "unreadable created_date"


def _problems(patched: PipelineSpec, row: dict[str, str]) -> list[str]:
    """What stops this row from standing alone: the checks the patched engine fails on this row
    by itself, or the reason it cannot be read. Empty means the row is clean."""
    try:
        results = engine.run(patched, [row], [], case_now(case_day(patched, [row]))).checks
    except (ToolError, ValueError, OverflowError):
        return [UNREADABLE]
    return [r.name for r in failed(results)]


def _counts(problems: Counter[str]) -> str:
    return ", ".join(f"{name} in {n}" for name, n in problems.most_common())


def choose_rows(
    original: PipelineSpec,
    patched: PipelineSpec,
    rows: list[dict[str, str]],
    edited: list[str],
) -> list[dict[str, str]]:
    """The regression rows, chosen by the code and never by the model, from CLEAN rows only: a row
    is clean when the patched engine run on that row alone fails no check and raises nothing.
    Clean rows the patch changes come first, then clean rows it leaves alone, each in rank order
    (rows that send every edited column's source field first, then the fewest empty fields, then
    the key), one row per key, up to CASE_SIZE; the case is listed by key.

    Why it holds: every chosen row passes the row-level checks on its own and the keys are
    distinct, so any subset of them has no failing row for null rates, ranges, casts, required
    values and date order, and the required fields are present in each row; volume has no history,
    and freshness and created-not-future use the subset's own `case_now`. The cost: a valid patch
    whose every changed row also has a defect of its own is refused, and the agent files an issue
    for a person instead."""
    key_field = patched.column(patched.source.key).source
    if len(rows) < MIN_CASE:
        raise ToolError(
            f"the failing batch has {len(rows)} rows; a regression case needs at least {MIN_CASE}"
        )
    edited_sources = [patched.column(c).source for c in edited]
    read = [c.source for c in patched.columns]

    def rank(i: int) -> tuple[bool, int, str]:
        row = rows[i]
        lacks_edited = not all(row.get(f) for f in edited_sources)
        return lacks_edited, sum(1 for f in read if not row.get(f)), row.get(key_field, "")

    changed = _changed_rows(original, patched, rows)
    if not any(changed):
        raise ToolError(
            "the patch changes none of today's rows, so it does not explain the failing check"
        )
    order = sorted(range(len(rows)), key=rank)
    taken: dict[str, dict[str, str]] = {}
    problems: Counter[str] = Counter()
    for same in (False, True):
        for i in order:
            key = rows[i].get(key_field, "")
            if changed[i] == same or key in taken:
                continue
            if len(taken) >= CASE_SIZE:
                break
            found = _problems(patched, rows[i])
            if not found:
                taken[key] = rows[i]
            elif not same:
                problems.update(found)
        if not same and not taken:
            raise ToolError(
                f"the patch changes {sum(changed)} of today's rows, but no row it changes passes "
                f"every check on its own ({_counts(problems)}), so no regression case can be "
                "built from today's rows: the case is built only from rows that pass alone"
            )
    if len(taken) < MIN_CASE:
        raise ToolError(
            f"only {len(taken)} distinct keys among today's rows pass every check on their own; "
            f"a regression case needs at least {MIN_CASE}"
        )
    subset = sorted(taken.values(), key=lambda r: r.get(key_field, ""))
    left = failed(engine.run(patched, subset, [], case_now(case_day(patched, subset))).checks)
    if left:
        raise ToolError(
            "the chosen rows fail checks under the patched spec: "
            + "; ".join(f"{r.name}: {r.detail}" for r in left[:3])
        )
    return subset


def build_case(
    original: PipelineSpec,
    patched: PipelineSpec,
    rows: list[dict[str, str]],
    edited: list[str],
) -> RegressionCase:
    subset = choose_rows(original, patched, rows, edited)
    now = case_now(case_day(patched, subset))
    expected = table_rows(engine.run(patched, subset, [], now).table)
    case = RegressionCase(subset, expected)
    if not reproduces(patched, case):
        raise ToolError("the regression case does not reproduce with the patched spec")
    return case


def reproduces(spec: PipelineSpec, case: RegressionCase) -> bool:
    got: Any = table_rows(
        engine.run(spec, case.rows, [], case_now(case_day(spec, case.rows))).table
    )
    return bool(json.loads(json.dumps(got)) == json.loads(json.dumps(case.expected)))


def case_reproduces(data: dict[str, Any]) -> bool:
    """A regression case file: the spec it carries turns its rows into its expected rows."""
    spec = PipelineSpec.from_yaml(data["spec"])
    return reproduces(spec, RegressionCase(data["rows"], data["expected"]))
