"""The analyst's frozen universe: 25 complaint types by the 59 regular community districts.

The complaint types are chosen once, from one SoQL `$group` query over a fixed span, and kept in
data/universe.json; they are never recomputed from the loaded window.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from pothole.socrata.client import Socrata
from pothole.spec.normalise import BOARDS_PER_BOROUGH

DATASET = "erm2-nwe9"
SPAN = ("2026-08-01", "2026-11-01")  # created_date from the first, before the second
TOP = 25
DEFAULT_PATH = Path(__file__).parents[3] / "data" / "universe.json"


@dataclass(frozen=True)
class Universe:
    """Complaint types and (borough, district number) pairs."""

    complaint_types: frozenset[str]
    boards: frozenset[tuple[str, int]]


def regular_boards() -> frozenset[tuple[str, int]]:
    return frozenset((b, n) for b, count in BOARDS_PER_BOROUGH.items() for n in range(1, count + 1))


def freeze(client: Socrata, path: Path = DEFAULT_PATH, dataset: str = DATASET) -> Universe:
    """Ask the source for the TOP complaint types over SPAN and write them to `path`."""
    start, end = SPAN
    rows = client.query(
        dataset,
        {
            "$select": "complaint_type, count(*) AS n",
            "$where": f"created_date >= '{start}T00:00:00' AND created_date < '{end}T00:00:00' "
            "AND complaint_type IS NOT NULL",
            "$group": "complaint_type",
            "$order": "n DESC, complaint_type",
            "$limit": str(TOP),
        },
    )
    types = [r["complaint_type"] for r in rows]
    if len(types) != TOP:
        raise ValueError(f"expected {TOP} complaint types, the source returned {len(types)}")
    body = {
        "span": {"from": start, "before": end},
        "complaint_types": [{"name": r["complaint_type"], "rows": int(r["n"])} for r in rows],
        "boards": [{"borough": b, "number": n} for b, n in sorted(regular_boards())],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=2) + "\n")
    return load(path)


def load(path: Path = DEFAULT_PATH) -> Universe:
    body = json.loads(path.read_text())
    return Universe(
        frozenset(t["name"] for t in body["complaint_types"]),
        frozenset((b["borough"], int(b["number"])) for b in body["boards"]),
    )
