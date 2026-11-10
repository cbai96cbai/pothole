"""Incremental load of the raw window by keyset paging on (:updated_at, :id).

Each run starts one hour before the saved cursor and replays from there, so a row that became
visible late with an older timestamp is still read; upserts by key make the replay harmless.
W, the newest :updated_at in the window, is read once at the start: rows changed after it wait
for the next run. One transaction per page writes the rows and the cursor together; the
generation and `last_success` are written once, after the whole load and its checks.

Row-level checks run on every page and the worst result per check is kept. Freshness and volume
describe the whole window, not a page, so they are computed from the store after the load:
volume compares the analysis day's row count with the same weekday in earlier weeks.
"""

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from pothole.logs import get_logger
from pothole.socrata import meta
from pothole.socrata.client import Socrata, SourceRejected
from pothole.spec import checks as ck
from pothole.spec.checks import CheckResult
from pothole.spec.model import PipelineSpec
from pothole.store import counts, db

PAGE_SIZE = 50_000
OVERLAP = timedelta(hours=1)
EPOCH = "1970-01-01T00:00:00.000Z"
ANALYSIS_LAG_DAYS = 2
_RANK = {"pass": 0, "warn": 1, "fail": 2}
_BATCH_LEVEL = ("freshness", "volume")

log = get_logger(__name__)


@dataclass
class LoadResult:
    rows: int
    pages: int
    cursor: tuple[str, str] | None
    generation: str
    checks: list[CheckResult] = field(default_factory=list)


def window_start(spec: PipelineSpec, now: datetime) -> str:
    return f"{now.date() - timedelta(days=spec.source.window_days)}T00:00:00"


def _shift(stamp: str, delta: timedelta) -> str:
    moved = datetime.fromisoformat(stamp.replace("Z", "+00:00")) + delta
    return moved.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moved.microsecond // 1000:03d}Z"


def keyset_where(ts: str, id_: str, upper: str, start: str) -> str:
    """Rows after (ts, id_) in (:updated_at, :id) order, up to W, inside the window.

    The source rejects an :id literal that is not a real row id, so the start of a run, whose
    id is empty (before every id), compares :updated_at alone with >=.
    """
    t, w = db.literal(ts), db.literal(upper)
    after = (
        f":updated_at >= {t}"
        if not id_
        else f"((:updated_at > {t}) OR (:updated_at = {t} AND :id > {db.literal(id_)}))"
    )
    return f"{after} AND :updated_at <= {w} AND created_date >= {db.literal(start)}"


def _keep_worst(worst: dict[str, CheckResult], results: list[CheckResult]) -> None:
    for r in results:
        if r.name in _BATCH_LEVEL:
            continue
        old = worst.get(r.name)
        if old is None or _RANK[r.level] > _RANK[old.level]:
            worst[r.name] = r


def _window_checks(con: db.Connection, spec: PipelineSpec, now: datetime) -> list[CheckResult]:
    out: list[CheckResult] = []
    c = spec.checks
    if c.freshness_max_hours is not None:
        newest = con.execute(f"SELECT max(created_date) FROM {db.RAW}").fetchone()
        values = [newest[0]] if newest and newest[0] is not None else []
        out.append(ck.freshness(ck.Cast(values, [], [], set()), c.freshness_max_hours, now))
    if c.volume is not None:
        day = now.date() - timedelta(days=ANALYSIS_LAG_DAYS)
        totals = counts.daily_totals(con)
        history = [totals[d] for d in sorted(totals) if d < day and d.weekday() == day.weekday()]
        out.append(ck.volume(c.volume, totals.get(day, 0), history[-c.volume.weeks :]))
    return out


def incremental(
    con: db.Connection,
    socrata: Socrata,
    spec: PipelineSpec,
    now: datetime,
    page_size: int = PAGE_SIZE,
) -> LoadResult:
    """Load what changed since the last run; a store with no cursor loads the whole window.

    now: New York local time, naive. Raises SourceUnavailable or SourceRejected from the client,
    leaving the cursor at the last committed page and last_success unchanged.
    """
    dataset = spec.source.dataset
    db.ensure_raw(con, spec)
    start = window_start(spec, now)
    saved = db.cursor(con)
    ts, id_ = (EPOCH, "") if saved is None else (_shift(saved[0], -OVERLAP), "")
    upper = meta.max_updated_at(socrata, dataset, f"created_date >= {db.literal(start)}")
    select = db.select_clause(spec)
    worst: dict[str, CheckResult] = {}
    rows_read = pages = 0
    while True:
        page = socrata.query(
            dataset,
            {
                "$select": select,
                "$where": keyset_where(ts, id_, upper, start),
                "$order": ":updated_at,:id",
                "$limit": str(page_size),
            },
        )
        if not page:
            break
        try:
            ts, id_ = page[-1][":updated_at"], page[-1][":id"]
        except KeyError as exc:
            raise SourceRejected(f"{dataset}: a row came back without {exc}") from exc
        con.begin()
        try:
            _keep_worst(worst, db.upsert(con, spec, page, now))
            db.set_cursor(con, ts, id_)
            con.commit()
        except BaseException:
            con.rollback()
            raise
        rows_read += len(page)
        pages += 1
        log.info("page loaded", extra={"page": pages, "rows": len(page), "cursor": ts})
        if len(page) < page_size:
            break
    results = [*worst.values(), *_window_checks(con, spec, now)]
    generation = str(uuid.uuid4())
    cutoff = f"{now.date() - timedelta(days=spec.source.window_days)}"
    con.begin()
    try:
        con.execute(f"DELETE FROM {db.RAW} WHERE created_date < CAST(? AS DATE)", [cutoff])
        db.set_meta(con, "generation", generation)
        db.set_meta(con, "last_success", now.isoformat(timespec="seconds"))
        con.commit()
    except BaseException:
        con.rollback()
        raise
    return LoadResult(rows_read, pages, db.cursor(con), generation, results)


def last_success(con: db.Connection) -> date | None:
    value = db.get_meta(con, "last_success")
    return None if value is None else datetime.fromisoformat(value).date()
