"""Writes the frozen drill catalog and its freeze file. Run once; nothing at run time reads this
module: the faults take their parameters from data/drills/catalog.json."""

import hashlib
import json
import random
import zlib
from collections.abc import Callable
from typing import Any

from pothole.drills.catalog import BATCH_DIR, CATALOG, FREEZE, JUDGED_DAYS, ROOT, days

BOTH_DATES = ("created_date", "closed_date")
# One entry per variant. The first six variants of every family are the dev ones (test-split
# families use them as their test ones); the controls have nine, the last three being test.
RENAMES = [
    ("agency", "agency_code"),
    ("complaint_type", "complaint_type_name"),
    ("status", "case_status"),
    ("borough", "boro"),
    ("created_date", "created_at"),
    ("complaint_type", "issue_category"),
]
DATE_CHANGES: list[tuple[tuple[str, ...], str]] = [
    (BOTH_DATES, "%Y-%m-%dT%H:%M:%S"),
    (BOTH_DATES, "%Y-%m-%d %H:%M:%S"),
    (BOTH_DATES, "%m/%d/%Y %I:%M:%S %p"),
    (BOTH_DATES, "%m/%d/%Y %H:%M:%S"),
    (("created_date",), "%m/%d/%Y %I:%M:%S %p"),
    (("closed_date",), "%Y-%m-%d %H:%M:%S"),
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
    {k: k.lower() for k in _TITLE},
    {"BROOKLYN": "BK", "QUEENS": "QN", "MANHATTAN": "MN", "BRONX": "BX", "STATEN ISLAND": "SI"},
    {"BROOKLYN": "K", "QUEENS": "Q", "MANHATTAN": "M", "BRONX": "X", "STATEN ISLAND": "R"},
    {"MANHATTAN": "1", "BRONX": "2", "BROOKLYN": "3", "QUEENS": "4", "STATEN ISLAND": "5"},
    {k: k + " " for k in _TITLE},
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
X, Y = "x_coordinate_state_plane", "y_coordinate_state_plane"
COMMA_FIELDS: list[tuple[tuple[str, ...], float]] = [
    ((X,), 1.0),
    ((Y,), 1.0),
    ((X,), 0.6),
    ((Y,), 0.3),
    ((X, Y), 0.8),
    ((X, Y), 0.1),
]
PARTIAL_KEEP = 0.3
PARTIAL_MODES = ["random", "head", "tail", "random", "head", "tail"]
EMPTY_KEEP = [0, 1, 2, 5, 10, 25]
SPIKE_FACTORS = [2.5, 3, 4, 2.5, 3, 4, 2.5, 3, 4]
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


JUDGED = JUDGED_DAYS
RULE = (
    "seed = crc32('<family>:<variant>'). The six recorded days are shuffled by "
    "random.Random('pothole-drills:<family>'); variant v is seen on day v mod 6 of that order; "
    "three of the other five days, drawn by random.Random(seed) and sorted, are judged."
)
# family -> (right action, dev variants, test variants). Controls have nine variants.
FAMILIES: dict[str, tuple[str, range, range]] = {
    "renamed_field": ("patch", range(6), range(0)),
    "date_format": ("patch", range(6), range(0)),
    "borough_recoding": ("patch", range(6), range(0)),
    "comma_thousands": ("patch", range(0), range(6)),
    "latlon_swapped": ("patch", range(0), range(6)),
    "field_dropped": ("issue", range(6), range(0)),
    "partial_load": ("issue", range(6), range(0)),
    "null_flood": ("issue", range(0), range(6)),
    "source_empty": ("issue", range(0), range(6)),
    "spike": ("no_change", range(6), range(6, 9)),
    "benign_field": ("nothing", range(6), range(6, 9)),
}


def params(family: str, v: int) -> dict[str, Any]:
    makers: dict[str, Callable[[], dict[str, Any]]] = {
        "renamed_field": lambda: {"column": RENAMES[v][0], "new": RENAMES[v][1]},
        "date_format": lambda: {"fields": list(DATE_CHANGES[v][0]), "format": DATE_CHANGES[v][1]},
        "borough_recoding": lambda: {"recode": BOROUGH_RECODINGS[v]},
        "comma_thousands": lambda: {
            "fields": list(COMMA_FIELDS[v][0]),
            "share": COMMA_FIELDS[v][1],
        },
        "latlon_swapped": lambda: {},
        "field_dropped": lambda: {"fields": list(DROPPED[v])},
        "partial_load": lambda: {"keep": PARTIAL_KEEP, "mode": PARTIAL_MODES[v]},
        "null_flood": lambda: {"field": NULL_FLOODS[v][0], "share": NULL_FLOODS[v][1]},
        "source_empty": lambda: {"keep": EMPTY_KEEP[v]},
        "spike": lambda: {"factor": SPIKE_FACTORS[v]},
        "benign_field": lambda: {"field": BENIGN_FIELDS[v][0], "value": BENIGN_FIELDS[v][1]},
    }
    return makers[family]()


def build() -> dict[str, Any]:
    all_days = days()
    cases: list[dict[str, Any]] = []
    for family, (action, dev, test) in FAMILIES.items():
        order = list(all_days)
        random.Random(f"pothole-drills:{family}").shuffle(order)
        for split, variants in (("dev", dev), ("test", test)):
            for v in variants:
                seed = zlib.crc32(f"{family}:{v}".encode())
                seen = order[v % len(order)]
                others = [d for d in all_days if d != seen]
                judged = sorted(random.Random(seed).sample(others, JUDGED))
                cases.append(
                    {
                        "id": f"{family}-{v}",
                        "family": family,
                        "variant": v,
                        "seed": seed,
                        "batch_seen": seen,
                        "batch_judged": judged,
                        "expected_action": action,
                        "split": split,
                        "params": params(family, v),
                    }
                )
    return {"rule": RULE, "cases": cases}


def freeze_hashes() -> dict[str, str]:
    """sha256 of everything the cases depend on: the catalog, the spec and the six batches."""
    files = [CATALOG, BATCH_DIR / "spec.yaml", *sorted(BATCH_DIR.glob("*.jsonl.gz"))]
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


def write() -> None:
    CATALOG.parent.mkdir(parents=True, exist_ok=True)
    CATALOG.write_text(json.dumps(build(), indent=2) + "\n")
    FREEZE.write_text(json.dumps(freeze_hashes(), indent=2) + "\n")


if __name__ == "__main__":
    write()
