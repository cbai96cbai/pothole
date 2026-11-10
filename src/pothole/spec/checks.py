"""The checks. Each returns one CheckResult whose detail names the field and gives counts."""

import statistics
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pothole.spec.model import Column, PipelineSpec, Volume

Level = Literal["pass", "warn", "fail"]
FAIL_SHARE = 0.01
SAMPLE = 5


@dataclass
class CheckResult:
    name: str
    level: Level
    detail: str
    sample_keys: list[str]


@dataclass
class Cast:
    values: list[Any]
    cast_failed: list[str]
    outside_values: list[str]
    outside_raw: set[str]


def _result(name: str, level: Level, detail: str, keys: list[str] | None = None) -> CheckResult:
    return CheckResult(name, level, detail, (keys or [])[:SAMPLE])


def schema(spec: PipelineSpec, rows: list[dict[str, str]]) -> CheckResult:
    seen = {field for row in rows for field in row}
    mapped = {c.source for c in spec.columns}
    missing = [c.source for c in spec.columns if c.required and c.source not in seen]
    if missing:
        return _result(
            "schema",
            "fail",
            f"required source fields missing from all {len(rows)} rows: "
            + ", ".join(sorted(missing)),
        )
    unknown = sorted(seen - mapped)
    if unknown:
        return _result(
            "schema", "warn", f"{len(unknown)} source fields not in the spec: " + ", ".join(unknown)
        )
    return _result("schema", "pass", f"all required source fields present in {len(rows)} rows")


def cast_and_values(col: Column, cast: Cast, total: int) -> list[CheckResult]:
    out: list[CheckResult] = []
    if col.type != "text":
        n = len(cast.cast_failed)
        share = n / total if total else 0.0
        level: Level = "fail" if share > FAIL_SHARE else "pass"
        out.append(
            _result(
                f"cast:{col.name}",
                level,
                f"{col.name}: {n} of {total} rows could not be read as {col.type}",
                cast.cast_failed,
            )
        )
    if col.values is not None:
        n = len(cast.outside_values)
        raws = ", ".join(sorted(cast.outside_raw)[:SAMPLE])
        detail = f"{col.name}: {n} of {total} rows have values outside the listed ones"
        out.append(
            _result(
                f"values:{col.name}",
                "fail" if n else "pass",
                detail + (f" ({raws})" if n else ""),
                cast.outside_values,
            )
        )
    return out


def null_rate(name: str, cast: Cast, limit: float, keys: list[str]) -> CheckResult:
    total = len(cast.values)
    empty = [k for k, v in zip(keys, cast.values, strict=True) if v is None]
    rate = len(empty) / total if total else 0.0
    detail = f"{name}: {len(empty)} of {total} rows empty ({rate:.1%}), allowed {limit:.1%}"
    return _result(f"null_rate:{name}", "fail" if rate > limit else "pass", detail, empty)


def in_range(name: str, cast: Cast, low: float, high: float, keys: list[str]) -> CheckResult:
    present = [(k, v) for k, v in zip(keys, cast.values, strict=True) if v is not None]
    bad = [k for k, v in present if not low <= v <= high]
    share = len(bad) / len(present) if present else 0.0
    detail = f"{name}: {len(bad)} of {len(present)} values outside [{low}, {high}]"
    return _result(f"range:{name}", "fail" if share > FAIL_SHARE else "pass", detail, bad)


def freshness(created: Cast, max_hours: float, now: datetime) -> CheckResult:
    present = [v for v in created.values if v is not None]
    if not present:
        return _result("freshness", "fail", "created_date: no readable values, age unknown")
    age = (now - max(present)).total_seconds() / 3600
    detail = f"created_date: newest record is {age:.1f} hours old, allowed {max_hours:g}"
    return _result("freshness", "fail" if age > max_hours else "pass", detail)


def created_not_future(created: Cast, keys: list[str], now: datetime) -> CheckResult:
    bad = [k for k, v in zip(keys, created.values, strict=True) if v is not None and v > now]
    detail = f"created_date: {len(bad)} of {len(keys)} rows are dated in the future"
    return _result("created_not_future", "fail" if bad else "pass", detail, bad)


def closed_not_before_created(casts: dict[str, Cast], keys: list[str]) -> CheckResult:
    pairs = zip(keys, casts["created_date"].values, casts["closed_date"].values, strict=True)
    bad = [k for k, c, d in pairs if c is not None and d is not None and d < c]
    detail = f"closed_date: {len(bad)} of {len(keys)} rows closed before they were created"
    return _result("closed_not_before_created", "fail" if bad else "pass", detail, bad)


def unique_key(name: str, keys: list[str]) -> CheckResult:
    seen: set[str] = set()
    dup: list[str] = []
    for k in keys:
        if k in seen and k not in dup:
            dup.append(k)
        seen.add(k)
    detail = f"{name}: {len(dup)} keys repeat within the batch of {len(keys)} rows"
    return _result("unique_key", "fail" if dup else "pass", detail, dup)


def volume(spec: Volume, count: int, history: list[int]) -> CheckResult:
    if not history:
        return _result("volume", "pass", f"{count} rows; no earlier same-weekday counts to compare")
    typical = statistics.median(history)
    low, high = spec.low * typical, spec.high * typical
    detail = (
        f"{count} rows against a typical {typical:g} over {len(history)} earlier "
        f"same-weekday counts; allowed {low:g} to {high:g}"
    )
    return _result("volume", "fail" if not low <= count <= high else "pass", detail)
