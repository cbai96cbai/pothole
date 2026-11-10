"""Faults: each one changes the source rows the way a real upstream change would.

What a fault does is set by its `params`, which come from the frozen catalog."""

import random
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pothole.spec.checks import CheckResult
from pothole.spec.model import DATE_FORMATS

Row = dict[str, Any]
SOURCE_DATE = "%Y-%m-%dT%H:%M:%S.%f"


@dataclass(frozen=True)
class Fault:
    family: str
    variant: int
    seed: int
    params: dict[str, Any] = field(default_factory=dict)


Apply = Callable[[list[Row], dict[str, Any], random.Random], list[Row]]


def _rename(rows: list[Row], p: dict[str, Any], rng: random.Random) -> list[Row]:
    return [{(p["new"] if k == p["column"] else k): v for k, v in r.items()} for r in rows]


def _date_format(rows: list[Row], p: dict[str, Any], rng: random.Random) -> list[Row]:
    for r in rows:
        for name in p["fields"]:
            if r.get(name):
                r[name] = datetime.strptime(r[name], SOURCE_DATE).strftime(p["format"])
    return rows


def _borough(rows: list[Row], p: dict[str, Any], rng: random.Random) -> list[Row]:
    for r in rows:
        if "borough" in r:
            r["borough"] = p["recode"].get(r["borough"], r["borough"])
    return rows


def _comma(rows: list[Row], p: dict[str, Any], rng: random.Random) -> list[Row]:
    for r in rows:
        for name in p["fields"]:
            if r.get(name) and rng.random() < p["share"]:
                r[name] = f"{int(r[name]):,}"
    return rows


def _swap(rows: list[Row], p: dict[str, Any], rng: random.Random) -> list[Row]:
    out: list[Row] = []
    for r in rows:
        lat, lon = r.pop("latitude", None), r.pop("longitude", None)
        if lon is not None:
            r["latitude"] = lon
        if lat is not None:
            r["longitude"] = lat
        out.append(r)
    return out


def _dropped(rows: list[Row], p: dict[str, Any], rng: random.Random) -> list[Row]:
    for r in rows:
        for name in p["fields"]:
            r.pop(name, None)
    return rows


def _partial(rows: list[Row], p: dict[str, Any], rng: random.Random) -> list[Row]:
    keep = round(len(rows) * p["keep"])
    if p["mode"] == "head":
        return rows[:keep]
    if p["mode"] == "tail":
        return rows[len(rows) - keep :]
    return [rows[i] for i in sorted(rng.sample(range(len(rows)), keep))]


def _flood(rows: list[Row], p: dict[str, Any], rng: random.Random) -> list[Row]:
    for r in rows:
        if rng.random() < p["share"]:
            r.pop(p["field"], None)
    return rows


def _empty(rows: list[Row], p: dict[str, Any], rng: random.Random) -> list[Row]:
    return rng.sample(rows, p["keep"])


def _spike(rows: list[Row], p: dict[str, Any], rng: random.Random) -> list[Row]:
    extra: list[Row] = []
    for n in range(round(len(rows) * (p["factor"] - 1))):
        row = dict(rng.choice(rows))
        row["unique_key"] = f"S{n}"
        if ":id" in row:
            row[":id"] = f"spike-{n}"
        extra.append(row)
    return rows + extra


def _benign(rows: list[Row], p: dict[str, Any], rng: random.Random) -> list[Row]:
    for r in rows:
        r[p["field"]] = p["value"]
    return rows


_APPLY: dict[str, Apply] = {
    "renamed_field": _rename,
    "date_format": _date_format,
    "borough_recoding": _borough,
    "comma_thousands": _comma,
    "latlon_swapped": _swap,
    "field_dropped": _dropped,
    "partial_load": _partial,
    "null_flood": _flood,
    "source_empty": _empty,
    "spike": _spike,
    "benign_field": _benign,
}
FAMILY_NAMES = list(_APPLY)


def field_types(rows: list[Row]) -> dict[str, str]:
    """What the values of each source field look like: int, float, timestamp, object or text."""
    values: dict[str, list[Any]] = {}
    for r in rows[:500]:
        for k, v in r.items():
            if v not in (None, ""):
                values.setdefault(k, []).append(v)
    return {k: _kind(v) for k, v in values.items()}


def _kind(values: list[Any]) -> str:
    if any(not isinstance(v, str) for v in values):
        return "object"
    for kind, test in (("int", int), ("float", float), ("timestamp", _is_date)):
        try:
            for v in values:
                test(v)
        except ValueError:
            continue
        return kind
    return "text"


def _is_date(text: str) -> None:
    for fmt in DATE_FORMATS:
        try:
            datetime.strptime(text, fmt)
            return
        except ValueError:
            continue
    raise ValueError(text)


def inject(batch: list[Row], fault: Fault) -> tuple[list[Row], dict[str, str]]:
    """The batch after the fault, and the fields the source now sends with their types.

    The same fault applied to any day's rows changes them by the same rule.
    """
    rng = random.Random(f"{fault.seed}:{fault.family}")
    rows = _APPLY[fault.family]([dict(r) for r in batch], fault.params, rng)
    return rows, field_types(rows or batch)


def intended_checks(fault: Fault) -> tuple[list[str], str]:
    """The check names that must show the fault, and the level they show it at."""
    p = fault.params
    return {
        "renamed_field": (["schema"], "fail"),
        "date_format": ([f"cast:{c}" for c in p.get("fields", [])], "fail"),
        "borough_recoding": (["values:borough"], "fail"),
        "comma_thousands": ([f"cast:{c}" for c in p.get("fields", [])], "fail"),
        "latlon_swapped": (["range:latitude", "range:longitude"], "fail"),
        "field_dropped": (["schema"], "fail"),
        "partial_load": (["volume"], "fail"),
        "null_flood": ([f"required:{p.get('field')}"], "fail"),
        "source_empty": (["volume"], "fail"),
        "spike": (["volume"], "fail"),
        "benign_field": (["schema"], "warn"),
    }[fault.family]


def detected(fault: Fault, checks: list[CheckResult]) -> bool:
    """Every intended check shows the fault; the benign control shows its new field in a warning."""
    names, level = intended_checks(fault)
    at_level = {c.name: c for c in checks if c.level == level}
    if not all(n in at_level for n in names):
        return False
    if fault.family == "benign_field":
        return fault.params["field"] in at_level["schema"].detail
    return True
