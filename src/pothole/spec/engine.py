"""Run a spec over a batch of raw rows: cast the columns, then run every check."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pyarrow as pa

from pothole.spec import checks as ck
from pothole.spec.checks import CheckResult
from pothole.spec.model import Column, PipelineSpec

__all__ = ["CheckResult", "EngineResult", "run"]

_ARROW = {
    "text": pa.string(),
    "int": pa.int64(),
    "float": pa.float64(),
    "timestamp": pa.timestamp("us"),
}


@dataclass
class EngineResult:
    table: pa.Table
    checks: list[CheckResult]


def _cast(col: Column, raw: str) -> Any:
    if col.type == "text":
        return raw
    if col.type == "timestamp":
        return datetime.strptime(raw, col.format) if col.format else datetime.fromisoformat(raw)
    text = raw.replace(",", "") if col.format == "comma_thousands" else raw
    number = float(text)
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError(raw)
    if col.type == "float":
        return number
    return int(text)


def _column(col: Column, rows: list[dict[str, str]], keys: list[str]) -> ck.Cast:
    values: list[Any] = []
    bad: list[str] = []
    outside: list[str] = []
    outside_raw: set[str] = set()
    for row, key in zip(rows, keys, strict=True):
        raw = row.get(col.source)
        if raw is None or raw == "":
            values.append(None)
            continue
        if col.values is not None:
            if raw not in col.values:
                outside.append(key)
                outside_raw.add(raw)
                values.append(None)
                continue
            raw = col.values[raw]
        try:
            values.append(_cast(col, raw))
        except ValueError:
            bad.append(key)
            values.append(None)
    return ck.Cast(values, bad, outside, outside_raw)


def run(
    spec: PipelineSpec,
    rows: list[dict[str, str]],
    history: list[int],
    now: datetime | None = None,
) -> EngineResult:
    """history: same-weekday row counts, oldest first; only the last `volume.weeks` are used.

    now: New York local time, naive (the dataset's timestamps carry no zone).
    """
    now = now or datetime.now(ZoneInfo("America/New_York")).replace(tzinfo=None)
    key_source = spec.column(spec.source.key).source
    keys = [row.get(key_source, "") for row in rows]
    casts = {c.name: _column(c, rows, keys) for c in spec.columns}
    results: list[CheckResult] = []
    c = spec.checks
    if c.schema_check:
        results.extend(ck.schema(spec, rows))
        results.extend(ck.required(spec, rows, keys))
    for col in spec.columns:
        results.extend(ck.cast_and_values(col, casts[col.name], len(rows)))
    for name, limit in c.null_rate_max.items():
        results.append(ck.null_rate(name, casts[name], limit, keys))
    for name, (low, high) in c.ranges.items():
        results.append(ck.in_range(name, casts[name], low, high, keys))
    if c.freshness_max_hours is not None:
        results.append(ck.freshness(casts["created_date"], c.freshness_max_hours, now))
    if c.created_not_future:
        results.append(ck.created_not_future(casts["created_date"], keys, now))
    if c.closed_not_before_created:
        results.append(ck.closed_not_before_created(casts, keys))
    if c.unique_key:
        results.append(ck.unique_key(spec.source.key, keys))
    if c.volume is not None:
        results.append(ck.volume(c.volume, len(rows), history[-c.volume.weeks :]))
    table = pa.table(
        {col.name: pa.array(casts[col.name].values, type=_ARROW[col.type]) for col in spec.columns}
    )
    return EngineResult(table=table, checks=results)
