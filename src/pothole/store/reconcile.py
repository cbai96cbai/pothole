"""Weekly check of the store against the source, day by day.

For each created day in the window the source's row count is compared with the store's. On a
mismatch that day's key list is fetched: keys the source no longer has are deleted, keys the
store lacks are fetched in full and upserted.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from pothole.socrata.client import Socrata
from pothole.spec.model import PipelineSpec
from pothole.store import counts, db
from pothole.store.load import PAGE_SIZE, window_start

FETCH_KEYS = 300


@dataclass
class ReconcileResult:
    days_checked: int
    days_fixed: int
    deleted: int
    added: int


def _day_where(day: date) -> str:
    return (
        f"created_date >= '{day}T00:00:00' AND created_date < '{day + timedelta(days=1)}T00:00:00'"
    )


def weekly(
    con: db.Connection, socrata: Socrata, spec: PipelineSpec, now: datetime | None = None
) -> ReconcileResult:
    now = now or datetime.now(ZoneInfo("America/New_York")).replace(tzinfo=None)
    dataset, key = spec.source.dataset, spec.source.key
    start = window_start(spec, now)
    db.ensure_raw(con, spec)
    upstream_rows = socrata.query(
        dataset,
        {
            "$select": "date_trunc_ymd(created_date) as d, count(*) as n",
            "$where": f"created_date >= {db.literal(start)}",
            "$group": "d",
            "$limit": str(PAGE_SIZE),
        },
    )
    upstream = {date.fromisoformat(r["d"][:10]): int(r["n"]) for r in upstream_rows}
    ours = {
        d: n for d, n in counts.daily_totals(con).items() if d >= date.fromisoformat(start[:10])
    }
    days = sorted(set(upstream) | set(ours))
    fixed = deleted = added = 0
    for day in days:
        if upstream.get(day, 0) == ours.get(day, 0):
            continue
        keys: set[str] = set()
        for page in socrata.pages(dataset, key, _day_where(day), key, PAGE_SIZE):
            keys.update(r[key] for r in page if key in r)
        held = {
            r[0]
            for r in con.execute(
                f"SELECT {db.quote(key)} FROM {db.RAW} WHERE CAST(created_date AS DATE) = ?",
                [day],
            ).fetchall()
        }
        extra, missing = sorted(held - keys), sorted(keys - held)
        for i in range(0, len(extra), FETCH_KEYS):
            chunk = extra[i : i + FETCH_KEYS]
            con.execute(
                f"DELETE FROM {db.RAW} WHERE {db.quote(key)} IN ({', '.join('?' * len(chunk))})",
                chunk,
            )
        for i in range(0, len(missing), FETCH_KEYS):
            chunk = missing[i : i + FETCH_KEYS]
            rows = socrata.query(
                dataset,
                {
                    "$select": db.select_clause(spec),
                    "$where": f"{key} in ({', '.join(db.literal(k) for k in chunk)})",
                    "$limit": str(len(chunk)),
                },
            )
            con.begin()
            try:
                db.upsert(con, spec, rows, now)
                con.commit()
            except BaseException:
                con.rollback()
                raise
            added += len(rows)
        deleted += len(extra)
        fixed += 1
    return ReconcileResult(len(days), fixed, deleted, added)
