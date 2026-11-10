"""Faults: each one changes the source rows the way a real upstream change would."""

import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pothole.spec.checks import CheckResult
from pothole.spec.model import DATE_FORMATS

Row = dict[str, Any]
SOURCE_DATE = "%Y-%m-%dT%H:%M:%S.%f"
BOTH_DATES = ("created_date", "closed_date")

# One entry per variant. The first six variants of every family are the dev ones (test-split
# families use them as their test ones); the controls have nine, the last three being test.
RENAMES = [
    ("agency", "agency_code"),
    ("complaint_type", "complaint_type_name"),
    ("status", "case_status"),
    ("borough", "boro"),
    ("created_date", "created_at"),
    ("status", "request_status"),
]
DATE_CHANGES: list[tuple[tuple[str, ...], str]] = [
    (BOTH_DATES, "%Y-%m-%dT%H:%M:%S"),
    (BOTH_DATES, "%Y-%m-%d %H:%M:%S"),
    (BOTH_DATES, "%m/%d/%Y %I:%M:%S %p"),
    (BOTH_DATES, "%m/%d/%Y %H:%M:%S"),
    (("created_date",), "%m/%d/%Y %I:%M:%S %p"),
    (("created_date",), "%Y-%m-%d %H:%M:%S"),
]
_TITLE = {
    "BROOKLYN": "Brooklyn",
    "QUEENS": "Queens",
    "MANHATTAN": "Manhattan",
    "BRONX": "Bronx",
    "STATEN ISLAND": "Staten Island",
}
BOROUGH_RECODINGS: list[dict[str, str]] = [
    _TITLE,
    {"BROOKLYN": "BK", "QUEENS": "QN", "MANHATTAN": "MN", "BRONX": "BX", "STATEN ISLAND": "SI"},
    {"BROOKLYN": "Brooklyn", "QUEENS": "Queens"},
    {"BRONX": "BX", "STATEN ISLAND": "SI"},
    {k: k.lower() for k in _TITLE},
    {"BROOKLYN": "BKLYN", "QUEENS": "QNS", "MANHATTAN": "MANH", "STATEN ISLAND": "STATEN IS"},
]
DROPPED: list[tuple[str, ...]] = [
    ("agency",),
    ("complaint_type",),
    ("status",),
    ("borough",),
    ("created_date",),
    ("agency", "status"),
]
NULL_FLOODS = [
    ("agency", 0.3),
    ("complaint_type", 0.5),
    ("status", 0.25),
    ("borough", 0.4),
    ("created_date", 0.2),
    ("status", 0.8),
]
COMMA_FIELDS: list[tuple[tuple[str, ...], float]] = [
    (("x_coordinate_state_plane",), 1.0),
    (("y_coordinate_state_plane",), 1.0),
    (("x_coordinate_state_plane", "y_coordinate_state_plane"), 1.0),
    (("x_coordinate_state_plane",), 0.5),
    (("y_coordinate_state_plane",), 0.5),
    (("x_coordinate_state_plane", "y_coordinate_state_plane"), 0.5),
]
PARTIAL_KEEP = 0.3
PARTIAL_MODES = ["random", "head", "tail", "random", "head", "tail"]
EMPTY_KEEP = [0, 1, 2, 5, 10, 25]
SPIKE_FACTOR = 2.5
BENIGN_FIELDS = [
    ("priority_flag", "N"),
    ("channel_detail", "phone"),
    ("response_hours", "24"),
    ("source_version", "v2"),
    ("geo_confidence", "0.9"),
    ("tags", "none"),
    ("sla_tier", "standard"),
    ("reviewed", "false"),
    ("batch_label", "daily"),
]


@dataclass(frozen=True)
class Fault:
    family: str
    variant: int
    seed: int


def _rename(rows: list[Row], f: Fault, rng: random.Random) -> list[Row]:
    old, new = RENAMES[f.variant]
    return [{(new if k == old else k): v for k, v in r.items()} for r in rows]


def _date_format(rows: list[Row], f: Fault, rng: random.Random) -> list[Row]:
    fields, fmt = DATE_CHANGES[f.variant]
    for r in rows:
        for name in fields:
            if r.get(name):
                r[name] = datetime.strptime(r[name], SOURCE_DATE).strftime(fmt)
    return rows


def _borough(rows: list[Row], f: Fault, rng: random.Random) -> list[Row]:
    recoding = BOROUGH_RECODINGS[f.variant]
    for r in rows:
        if "borough" in r:
            r["borough"] = recoding.get(r["borough"], r["borough"])
    return rows


def _comma(rows: list[Row], f: Fault, rng: random.Random) -> list[Row]:
    fields, share = COMMA_FIELDS[f.variant]
    for r in rows:
        for name in fields:
            if r.get(name) and rng.random() < share:
                r[name] = f"{int(r[name]):,}"
    return rows


def _swap(rows: list[Row], f: Fault, rng: random.Random) -> list[Row]:
    out: list[Row] = []
    for r in rows:
        lat, lon = r.pop("latitude", None), r.pop("longitude", None)
        if lon is not None:
            r["latitude"] = lon
        if lat is not None:
            r["longitude"] = lat
        out.append(r)
    return out


def _dropped(rows: list[Row], f: Fault, rng: random.Random) -> list[Row]:
    for r in rows:
        for name in DROPPED[f.variant]:
            r.pop(name, None)
    return rows


def _partial(rows: list[Row], f: Fault, rng: random.Random) -> list[Row]:
    keep = round(len(rows) * PARTIAL_KEEP)
    mode = PARTIAL_MODES[f.variant]
    if mode == "head":
        return rows[:keep]
    if mode == "tail":
        return rows[len(rows) - keep :]
    return [rows[i] for i in sorted(rng.sample(range(len(rows)), keep))]


def _flood(rows: list[Row], f: Fault, rng: random.Random) -> list[Row]:
    name, share = NULL_FLOODS[f.variant]
    for r in rows:
        if rng.random() < share:
            r.pop(name, None)
    return rows


def _empty(rows: list[Row], f: Fault, rng: random.Random) -> list[Row]:
    return rng.sample(rows, EMPTY_KEEP[f.variant])


def _spike(rows: list[Row], f: Fault, rng: random.Random) -> list[Row]:
    extra: list[Row] = []
    for n in range(round(len(rows) * (SPIKE_FACTOR - 1))):
        row = dict(rng.choice(rows))
        row["unique_key"] = f"S{n}"
        if ":id" in row:
            row[":id"] = f"spike-{n}"
        extra.append(row)
    return rows + extra


def _benign(rows: list[Row], f: Fault, rng: random.Random) -> list[Row]:
    name, value = BENIGN_FIELDS[f.variant]
    for r in rows:
        r[name] = value
    return rows


_APPLY: dict[str, Callable[[list[Row], Fault, random.Random], list[Row]]] = {
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
    rows = _APPLY[fault.family]([dict(r) for r in batch], fault, rng)
    return rows, field_types(rows or batch)


# What the checks must show for a fault to count as detected: (check name, its level).
_DETECTED_BY = {
    "renamed_field": ("schema", "fail"),
    "date_format": ("cast:", "fail"),
    "borough_recoding": ("values:borough", "fail"),
    "comma_thousands": ("cast:", "fail"),
    "latlon_swapped": ("range:", "fail"),
    "field_dropped": ("schema", "fail"),
    "partial_load": ("volume", "fail"),
    "null_flood": ("required:", "fail"),
    "source_empty": ("volume", "fail"),
    "spike": ("volume", "fail"),
    "benign_field": ("schema", "warn"),
}


def detected(fault: Fault, checks: list[CheckResult]) -> bool:
    prefix, level = _DETECTED_BY[fault.family]
    hits = [c for c in checks if c.name.startswith(prefix) and c.level == level]
    if fault.family == "benign_field":
        new = BENIGN_FIELDS[fault.variant][0]
        return any(new in c.detail for c in hits)
    return bool(hits)
