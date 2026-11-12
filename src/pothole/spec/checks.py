"""The checks. Each returns one CheckResult whose detail names the field and gives counts.

The schema check warns about a source field no column maps and `source.ignore` does not list
(a field the spec has never seen), so a warning always means something new.

Known limit: the schema check warns that an optional source field is missing from every row only
when the batch has at least OPTIONAL_MIN_ROWS rows (a 311 day has about 10,000), because a small
incremental page may hold only empty values for a field and would warn at random. A vanished
optional field is therefore warned on full pages and on the window as a whole, not on a handful
of rows. A vanished required field fails at any size.
"""

import re
import statistics
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pothole.spec.model import Column, PipelineSpec, Volume

Level = Literal["pass", "warn", "fail"]
FAIL_SHARE = 0.01
SAMPLE = 5
OPTIONAL_MIN_ROWS = 100


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


def _share_level(bad: int, total: int) -> Level:
    """One threshold for every row-level value check: over 1% of rows fails, any fewer warns."""
    if not bad:
        return "pass"
    return "fail" if bad / total > FAIL_SHARE else "warn"


def schema(spec: PipelineSpec, rows: list[dict[str, str]]) -> list[CheckResult]:
    seen = {field for row in rows for field in row}
    mapped = {c.source for c in spec.columns}
    missing = [c.source for c in spec.columns if c.required and c.source not in seen]
    system = {f for f in seen if f.startswith(":")}  # :id, :updated_at: the source's own
    unknown = sorted(seen - mapped - set(spec.source.ignore) - system)
    gone = sorted(c.source for c in spec.columns if not c.required and c.source not in seen)
    out: list[CheckResult] = []
    if missing:
        detail = f"required source fields missing from all {len(rows)} rows: "
        out.append(_result("schema", "fail", detail + ", ".join(sorted(missing))))
    if unknown:
        detail = f"{len(unknown)} source fields not in the spec: " + ", ".join(unknown)
        out.append(_result("schema" if not missing else "schema:unknown_fields", "warn", detail))
    if gone and len(rows) >= OPTIONAL_MIN_ROWS:  # a small page may simply hold only empty values
        detail = f"optional source fields missing from all {len(rows)} rows: " + ", ".join(gone)
        out.append(_result("schema:optional_missing", "warn", detail))
    if not out:
        out.append(
            _result("schema", "pass", f"all required source fields present in {len(rows)} rows")
        )
    return out


def required(spec: PipelineSpec, rows: list[dict[str, str]], keys: list[str]) -> list[CheckResult]:
    """A required column has a value in every row; absent from all rows is the schema check's."""
    seen = {field for row in rows for field in row}
    out: list[CheckResult] = []
    for col in spec.columns:
        if not col.required or col.source not in seen:
            continue
        empty = [k for k, r in zip(keys, rows, strict=True) if not r.get(col.source)]
        detail = f"{col.name}: required but missing from {len(empty)} of {len(rows)} rows"
        out.append(_result(f"required:{col.name}", "fail" if empty else "pass", detail, empty))
    return out


def cast_and_values(col: Column, cast: Cast, total: int) -> list[CheckResult]:
    out: list[CheckResult] = []
    if col.type != "text" or cast.cast_failed:  # a text column is reported only when it fails
        n = len(cast.cast_failed)
        out.append(
            _result(
                f"cast:{col.name}",
                _share_level(n, total),
                f"{col.name}: {n} of {total} rows could not be read as {col.type}",
                cast.cast_failed,
            )
        )
    if col.values is not None:
        n = len(cast.outside_values)
        raws = ", ".join(sorted(cast.outside_raw)[:SAMPLE])
        accepted = ", ".join(f"{raw} → {std}" for raw, std in sorted(col.values.items()))
        detail = f"{col.name}: {n} of {total} rows have values outside the accepted spellings"
        out.append(
            _result(
                f"values:{col.name}",
                "fail" if n else "pass",
                detail
                + (
                    f" ({raws}); accepted spellings and the value each becomes: {accepted}"
                    if n
                    else ""
                ),
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
    detail = f"{name}: {len(bad)} of {len(present)} values outside [{low}, {high}]"
    return _result(f"range:{name}", _share_level(len(bad), len(present)), detail, bad)


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
    return _result("created_not_future", _share_level(len(bad), len(keys)), detail, bad)


def closed_not_before_created(casts: dict[str, Cast], keys: list[str]) -> CheckResult:
    pairs = zip(keys, casts["created_date"].values, casts["closed_date"].values, strict=True)
    # Some agencies record closed_date only to the minute: compare at that precision.
    bad = [
        k
        for k, c, d in pairs
        if c is not None and d is not None and d < c.replace(second=0, microsecond=0)
    ]
    detail = f"closed_date: {len(bad)} of {len(keys)} rows closed before they were created"
    level = _share_level(len(bad), len(keys))
    return _result("closed_not_before_created", level, detail, bad)


def unique_key(name: str, keys: list[str]) -> CheckResult:
    seen: set[str] = set()
    dup: list[str] = []
    for k in keys:
        if k in seen and k not in dup:
            dup.append(k)
        seen.add(k)
    detail = f"{name}: {len(dup)} keys repeat within the batch of {len(keys)} rows"
    return _result("unique_key", "fail" if dup else "pass", detail, dup)


_VOLUME = re.compile(
    r"^(\d+) rows against a typical (\S+) over \d+ earlier same-weekday counts; "
    r"allowed (\S+) to (\S+)$"
)


@dataclass(frozen=True)
class VolumeReading:
    count: int
    typical: float
    low: float
    high: float


def read_volume(detail: str) -> VolumeReading:
    """The numbers in a failed volume check's detail (the one parser of the text `volume` writes).
    Raises ValueError for any other text."""
    m = _VOLUME.match(detail)
    if m is None:
        raise ValueError(f"cannot read the volume check's detail: {detail!r}")
    return VolumeReading(int(m.group(1)), float(m.group(2)), float(m.group(3)), float(m.group(4)))


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
