"""What a proposed patch must satisfy before it is accepted, and the shared batch helpers."""

import json
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


def _outputs(spec: PipelineSpec, rows: list[dict[str, str]], key: str) -> dict[str, Any]:
    """Each row's engine output by key: one run over the whole batch."""
    table = engine.run(spec, rows, [], datetime(2100, 1, 1)).table
    return {r[key]: r for r in table_rows(table)}


def _changed_by(
    original: PipelineSpec, patched: PipelineSpec, rows: list[dict[str, str]]
) -> set[str]:
    """Keys of the rows whose output differs between the original and the patched spec."""
    key = patched.source.key
    before, after = _outputs(original, rows, key), _outputs(patched, rows, key)
    return {k for k, row in after.items() if before.get(k) != row}


def choose_rows(
    original: PipelineSpec,
    patched: PipelineSpec,
    rows: list[dict[str, str]],
    edited: list[str],
) -> list[dict[str, str]]:
    """The regression rows, chosen by the code and never by the model: the most rows the patch
    changes (up to CASE_SIZE) that pass the checks under the patched spec, filled up to CASE_SIZE
    with the best-ranked rows it leaves alone. Rank: rows that send every edited column's source
    field first, then the fewest empty fields (a handful of rows must pass the checks' rates),
    then the key. The case is listed by key."""
    key_field = patched.column(patched.source.key).source
    if len(rows) < MIN_CASE:
        raise ToolError(
            f"the failing batch has {len(rows)} rows; a regression case needs at least {MIN_CASE}"
        )
    edited_sources = [patched.column(c).source for c in edited]
    read = [c.source for c in patched.columns]

    def rank(row: dict[str, str]) -> tuple[bool, int, str]:
        lacks_edited = not all(row.get(f) for f in edited_sources)
        return lacks_edited, sum(1 for f in read if not row.get(f)), row.get(key_field, "")

    changed_keys = _changed_by(original, patched, rows)
    if not changed_keys:
        raise ToolError(
            "the patch changes none of today's rows, so it does not explain the failing check"
        )
    ranked = sorted(rows, key=rank)
    changed = [r for r in ranked if r.get(key_field) in changed_keys]
    same = [r for r in ranked if r.get(key_field) not in changed_keys]
    last: list[CheckResult] = []
    for wanted in range(min(CASE_SIZE, len(changed)), 0, -1):
        left_out: set[str] = set()
        while True:  # each pass leaves out at least one new row or stops, so this ends
            take = [r for r in changed if r.get(key_field) not in left_out][:wanted]
            fill = [r for r in same if r.get(key_field) not in left_out][: CASE_SIZE - wanted]
            subset = sorted(take + fill, key=lambda r: r.get(key_field, ""))
            if len(take) < wanted or len(subset) < MIN_CASE:
                break
            last = failed(
                engine.run(patched, subset, [], case_now(case_day(patched, subset))).checks
            )
            if not last:
                return subset
            named = {k for r in last for k in r.sample_keys} - left_out
            if not named:
                break
            left_out |= named
    raise ToolError(
        "the rows the patch changes fail checks under the patched spec: "
        + "; ".join(f"{r.name}: {r.detail}" for r in last[:3])
    )


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
