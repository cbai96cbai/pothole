"""The counts the daily run keeps: the window's table for the analyst, and one immutable file per
analysed day.

`data/counts/<day>.csv.gz` holds only the rows created on that day, as counted at its own run
(later arrivals are not back-filled), sorted, with no timestamp in the gzip header so the same
table gives the same bytes. A committed file is never rewritten, and a day with no rows gets no
file. `data/state.json` names the store generation the last run derived its counts from.
"""

import gzip
import io
import json
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pacsv

from pothole.analyst import universe
from pothole.store import counts, db

COUNT_TYPES = {
    "date": pa.date32(),
    "borough": pa.string(),
    "board": pa.int64(),
    "complaint_type": pa.string(),
    "n": pa.int64(),
}


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n")


def counts_path(data: Path, day: date) -> Path:
    return data / "counts" / f"{day.isoformat()}.csv.gz"


def write_counts(path: Path, table: pa.Table) -> None:
    sink = io.BytesIO()
    pacsv.write_csv(table, sink)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(sink.getvalue(), mtime=0))


def read_counts(path: Path) -> pa.Table:
    options = pacsv.ConvertOptions(column_types=COUNT_TYPES)
    return pacsv.read_csv(io.BytesIO(gzip.decompress(path.read_bytes())), convert_options=options)


def refresh_counts(
    con: db.Connection, data: Path, generation: str, uni: universe.Universe, day: date
) -> pa.Table:
    """The whole window's counts, derived from the store, for the analyst; and `day`'s own file
    when it has none and there are rows to put in it."""
    table = counts.regenerate(con, uni)
    path = counts_path(data, day)
    if not path.exists():
        rows = table.filter(pc.equal(table["date"], pa.scalar(day, pa.date32())))
        if rows.num_rows:
            write_counts(path, rows)
    write_json(data / "state.json", {"generation": generation})
    return table


def write_vintage(data: Path, run_id: str, con: db.Connection, day: date) -> None:
    """One line per run: how many rows each created day held when this run finished loading."""
    held = [[d.isoformat(), n] for d, n in counts.vintage(con, day)]
    path = data / "vintages.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"run_id": run_id, "run_date": day.isoformat(), "held": held}) + "\n")
