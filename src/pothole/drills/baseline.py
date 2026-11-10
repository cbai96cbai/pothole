"""The non-AI baseline: fixed rules over exactly the repair agent's inputs (the spec, the failed
check results, sample rows and the source's fields). It reads nothing about the injected fault."""

import re
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any, cast

from pothole.repair.outcome import Op, RegressionCase, RepairOutcome, table_rows
from pothole.spec import engine, ops
from pothole.spec.checks import CheckResult
from pothole.spec.model import DATE_FORMATS, NUMBER_FORMATS, Column, PipelineSpec

MATCH_RATIO = 0.8
ABBREVIATIONS = {
    "BK": "BROOKLYN",
    "BKLYN": "BROOKLYN",
    "QN": "QUEENS",
    "QNS": "QUEENS",
    "MN": "MANHATTAN",
    "MAN": "MANHATTAN",
    "MANH": "MANHATTAN",
    "BX": "BRONX",
    "SI": "STATEN ISLAND",
    "STATEN IS": "STATEN ISLAND",
}
# Failures that follow from another one (a date that cannot be read is also not fresh).
_FOLLOW_ON = ("freshness", "created_not_future", "closed_not_before_created", "null_rate:")
_VOLUME = re.compile(r"^(\d+) rows .*allowed (\S+) to (\S+)$")
_REGRESSION_ROWS = 5


class _Unfixable(Exception):
    """A failure the rules cannot repair: the repairer files an issue."""


def _issue(reason: str) -> RepairOutcome:
    return RepairOutcome("issue", reason=reason)


def _volume_is_high(check: CheckResult) -> bool:
    m = _VOLUME.match(check.detail)
    return m is not None and int(m.group(1)) > float(m.group(3))


def _parses(col: Column, fmt: str, values: list[str]) -> bool:
    try:
        for v in values:
            if col.type == "timestamp":
                datetime.strptime(v, fmt)
            else:
                float(v.replace(",", "") if fmt == "comma_thousands" else v)
                if col.type == "int":
                    int(v.replace(",", "") if fmt == "comma_thousands" else v)
    except ValueError:
        return False
    return True


def _mapped(spec: PipelineSpec, check: CheckResult, source_fields: dict[str, str]) -> list[Op]:
    missing = [s.strip() for s in check.detail.rsplit(": ", 1)[1].split(",")]
    taken = {c.source for c in spec.columns}
    pool = [f for f in source_fields if not f.startswith(":") and f not in taken]
    out: list[Op] = []
    for source in missing:
        column = next(c.name for c in spec.columns if c.source == source)
        scored = [(SequenceMatcher(None, source.lower(), f.lower()).ratio(), f) for f in pool]
        ratio, best = max(scored, default=(0.0, ""))
        if ratio < MATCH_RATIO:
            raise _Unfixable(f"{source} is gone and no new field is close to its name")
        pool.remove(best)
        out.append(ops.MapField(column=column, source=best))
    return out


def _formatted(spec: PipelineSpec, check: CheckResult, rows: list[dict[str, Any]]) -> list[Op]:
    col = spec.column(check.name.split(":", 1)[1])
    values = [r[col.source] for r in rows if r.get(col.source)]
    for fmt in DATE_FORMATS if col.type == "timestamp" else NUMBER_FORMATS:
        if fmt != col.format and _parses(col, fmt, values):
            return [ops.SetFormat(column=col.name, format=fmt)]
    raise _Unfixable(f"no fixed format reads the values of {col.name}")


def _recoded(spec: PipelineSpec, check: CheckResult, rows: list[dict[str, Any]]) -> list[Op]:
    col = spec.column(check.name.split(":", 1)[1])
    known = {k.upper(): v for k, v in (col.values or {}).items()}
    out: list[Op] = []
    for raw in sorted({r[col.source] for r in rows if r.get(col.source)} - set(col.values or {})):
        key = raw.strip().upper()
        value = known.get(ABBREVIATIONS.get(key, key))
        if value is None:
            raise _Unfixable(f"{col.name}: {raw!r} matches no listed value")
        out.append(ops.AddValueMapping(column=col.name, raw=raw, value=value))
    return out


def _swapped(spec: PipelineSpec, rows: list[dict[str, Any]]) -> list[Op]:
    lat, lon = spec.column("latitude"), spec.column("longitude")
    (lat_lo, lat_hi), (lon_lo, lon_hi) = (
        spec.checks.ranges["latitude"],
        spec.checks.ranges["longitude"],
    )
    in_lat = [float(r[lon.source]) for r in rows if r.get(lon.source)]
    in_lon = [float(r[lat.source]) for r in rows if r.get(lat.source)]
    if (
        in_lat
        and in_lon
        and all(lat_lo <= v <= lat_hi for v in in_lat)
        and all(lon_lo <= v <= lon_hi for v in in_lon)
    ):
        return [
            ops.MapField(column="latitude", source=lon.source),
            ops.MapField(column="longitude", source=lat.source),
        ]
    raise _Unfixable("latitude and longitude are out of range and not simply swapped")


def _derive(
    spec: PipelineSpec,
    failed: list[CheckResult],
    rows: list[dict[str, Any]],
    fields: dict[str, str],
) -> list[Op]:
    out: list[Op] = []
    names = [c.name for c in failed]
    for check in failed:
        name = check.name
        if name == "schema":
            out += _mapped(spec, check, fields)
        elif name.startswith("cast:"):
            out += _formatted(spec, check, rows)
        elif name.startswith("values:"):
            out += _recoded(spec, check, rows)
        elif name.startswith("range:"):
            if name == "range:latitude" and "range:longitude" in names:
                out += _swapped(spec, rows)
            elif name != "range:longitude" or "range:latitude" not in names:
                raise _Unfixable(f"{name} fails on its own")
        elif not name.startswith(_FOLLOW_ON):
            raise _Unfixable(f"{name}: values are missing or repeated in the source")
    return out


def repair(
    spec: PipelineSpec,
    failed: list[CheckResult],
    sample_rows: list[dict[str, Any]],
    source_fields: dict[str, str],
) -> RepairOutcome:
    volume = next((c for c in failed if c.name == "volume"), None)
    if volume is not None:
        if not _volume_is_high(volume):
            return _issue("the source sent fewer rows than usual")
        if len(failed) == 1:
            return RepairOutcome("no_change", reason="more rows than usual, schema clean")
    try:
        edits = _derive(spec, [c for c in failed if c.name != "volume"], sample_rows, source_fields)
        if not edits:
            return _issue("no rule applies to the failed checks")
        patched = ops.apply(spec, edits)
    except (_Unfixable, ops.ForbiddenEdit) as err:
        return _issue(str(err))
    rows = sample_rows[:_REGRESSION_ROWS]
    now = datetime(2026, 11, 10)
    expected = table_rows(engine.run(patched, rows, [], now).table)
    return RepairOutcome(
        "patch",
        ops=edits,
        regression=RegressionCase(cast(list[dict[str, str]], rows), expected),
        reason="fixed rules",
    )
