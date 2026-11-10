"""The counts the daily run keeps: the window's table for the analyst, and each analysed day's
own counts, which live inside that day's analysis file (`data/analyst/<day>.json`), never apart
from it. `data/state.json` names the store generation the last run derived its counts from.
"""

import json
import os
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc

from pothole.analyst import universe
from pothole.analyst.finding import DayCount
from pothole.store import counts, db


def write_json(path: Path, payload: object) -> None:
    """Write to a temporary file, then replace: the file is whole or absent, never partial."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def refresh_counts(
    con: db.Connection, data: Path, generation: str, uni: universe.Universe
) -> pa.Table:
    """The whole window's counts, derived from the store, for the analyst."""
    table = counts.regenerate(con, uni)
    write_json(data / "state.json", {"generation": generation})
    return table


def day_counts(table: pa.Table, day: date) -> list[DayCount]:
    """The rows created on `day`, as counted now, sorted. They are stored in the day's analysis
    file, so the counts and the analysis that used them are one thing."""
    rows = table.filter(pc.equal(table["date"], pa.scalar(day, pa.date32())))
    return [
        DayCount(r["borough"], r["board"], r["complaint_type"], r["n"])
        for r in sorted(
            rows.to_pylist(), key=lambda r: (r["borough"], r["board"], r["complaint_type"])
        )
    ]


def write_vintage(data: Path, run_id: str, con: db.Connection, day: date) -> None:
    """One line per run: how many rows each created day held when this run finished loading."""
    held = [[d.isoformat(), n] for d, n in counts.vintage(con, day)]
    path = data / "vintages.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"run_id": run_id, "run_date": day.isoformat(), "held": held}) + "\n")
