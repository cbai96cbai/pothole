"""The local DuckDB store: the raw window table, the meta table, and the page upsert.

raw holds one row per source key: the spec's columns cast by the engine, then `_updated_at`
(UTC), `_created_at` (UTC, the first arrival's `:created_at`, never overwritten) and `_id`.
meta holds the cursor, the generation and `last_success` as text.
"""

from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa

from pothole.spec import engine
from pothole.spec.checks import CheckResult
from pothole.spec.model import PipelineSpec

RAW = "raw"
SYSTEM_FIELDS = (":id", ":updated_at", ":created_at")
_SQL_TYPE = {"text": "VARCHAR", "int": "BIGINT", "float": "DOUBLE", "timestamp": "TIMESTAMP"}
_EXTRA = (("_updated_at", "TIMESTAMP"), ("_created_at", "TIMESTAMP"), ("_id", "VARCHAR"))

Connection = duckdb.DuckDBPyConnection


def open(path: Path, read_only: bool = False) -> Connection:  # noqa: A001 - the store's own verb
    if read_only:
        return duckdb.connect(str(path), read_only=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE IF NOT EXISTS meta (key VARCHAR PRIMARY KEY, value VARCHAR)")
    return con


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def get_meta(con: Connection, key: str) -> str | None:
    row = con.execute("SELECT value FROM meta WHERE key = ?", [key]).fetchone()
    return None if row is None else str(row[0])


def set_meta(con: Connection, key: str, value: str) -> None:
    con.execute(
        "INSERT INTO meta VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        [key, value],
    )


def cursor(con: Connection) -> tuple[str, str] | None:
    ts, id_ = get_meta(con, "cursor_ts"), get_meta(con, "cursor_id")
    return None if ts is None or id_ is None else (ts, id_)


def set_cursor(con: Connection, ts: str, id_: str) -> None:
    set_meta(con, "cursor_ts", ts)
    set_meta(con, "cursor_id", id_)


def select_clause(spec: PipelineSpec) -> str:
    return ", ".join([*(c.source for c in spec.columns), *SYSTEM_FIELDS])


def ensure_raw(con: Connection, spec: PipelineSpec) -> bool:
    """Create raw to fit the spec. A table of another shape is dropped, with the cursor, so the
    window loads again in full; returns True then."""
    want = [(c.name, _SQL_TYPE[c.type]) for c in spec.columns] + list(_EXTRA)
    have = con.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name = ? ORDER BY ordinal_position",
        [RAW],
    ).fetchall()
    if [tuple(r) for r in have] == want:
        return False
    rebuilt = bool(have)
    if rebuilt:
        con.execute(f"DROP TABLE {RAW}")
        con.execute("DELETE FROM meta WHERE key IN ('cursor_ts', 'cursor_id')")
    columns = ", ".join(f"{quote(n)} {t}" for n, t in want)
    key = quote(spec.source.key)
    con.execute(f"CREATE TABLE {RAW} ({columns}, PRIMARY KEY ({key}))")
    return rebuilt


def _utc(raw: str | None) -> datetime | None:
    if not raw:
        return None
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)


def upsert(
    con: Connection, spec: PipelineSpec, rows: list[dict[str, Any]], now: datetime | None = None
) -> list[CheckResult]:
    """Cast the rows with the engine and upsert them by key; returns the engine's check results.

    The caller owns the transaction. Rows without a key are not stored (the checks report them);
    a key repeated in the batch keeps its last row.
    """
    plain = [{k: v for k, v in r.items() if k not in SYSTEM_FIELDS} for r in rows]
    result = engine.run(spec, plain, [], now)
    table = result.table
    for name, field in (("_updated_at", ":updated_at"), ("_created_at", ":created_at")):
        values = [_utc(r.get(field)) for r in rows]
        table = table.append_column(name, pa.array(values, type=pa.timestamp("us")))
    table = table.append_column("_id", pa.array([r.get(":id") for r in rows], type=pa.string()))
    last: dict[str, int] = {}
    for i, key in enumerate(table.column(spec.source.key).to_pylist()):
        if key is not None:
            last[key] = i
    table = table.take(pa.array(sorted(last.values()), type=pa.int64()))
    if table.num_rows:
        names = table.column_names
        cols = ", ".join(quote(n) for n in names)
        key = spec.source.key
        update = ", ".join(
            f"{quote(n)} = excluded.{quote(n)}" for n in names if n not in (key, "_created_at")
        )
        con.register("incoming", table)
        try:
            con.execute(
                f"INSERT INTO {RAW} ({cols}) SELECT {cols} FROM incoming "
                f"ON CONFLICT ({quote(key)}) DO UPDATE SET {update}"
            )
        finally:
            con.unregister("incoming")
    return result.checks
