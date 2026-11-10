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
import os
from collections.abc import Iterator
from contextlib import contextmanager
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
    con: db.Connection, data: Path, generation: str, uni: universe.Universe
) -> pa.Table:
    """The whole window's counts, derived from the store, for the analyst."""
    table = counts.regenerate(con, uni)
    write_json(data / "state.json", {"generation": generation})
    return table


@contextmanager
def day_counts(data: Path, table: pa.Table, day: date) -> Iterator[None]:
    """A day's counts file and its analysis are one pair. The file is staged from the same table
    the analysis uses and committed only if the block (the analysis and its file) succeeded; on
    any failure neither exists, so a retry computes both from the same data. A day that already
    has its file, or has no rows, stages nothing."""
    path = counts_path(data, day)
    staged: Path | None = None
    if not path.exists():
        rows = table.filter(pc.equal(table["date"], pa.scalar(day, pa.date32())))
        if rows.num_rows:
            staged = path.with_name(path.name + ".tmp")
            write_counts(staged, rows)
    try:
        yield
    except BaseException:
        if staged is not None:
            staged.unlink(missing_ok=True)
        raise
    if staged is not None:
        os.replace(staged, path)


def write_vintage(data: Path, run_id: str, con: db.Connection, day: date) -> None:
    """One line per run: how many rows each created day held when this run finished loading."""
    held = [[d.isoformat(), n] for d, n in counts.vintage(con, day)]
    path = data / "vintages.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"run_id": run_id, "run_date": day.isoformat(), "held": held}) + "\n")
