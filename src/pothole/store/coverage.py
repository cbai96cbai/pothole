"""Which days the store holds. The loader prunes every row created before a cutoff, so the first
day the store covers is that cutoff and the last is the day of the last finished load. A day inside
that span with no rows is a day with no requests; a day outside it is a day the store does not
know."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from pothole.store import db

START_KEY = "window_start"


@dataclass(frozen=True)
class Coverage:
    start: date
    end: date


def window_start(load_day: date, window_days: int) -> date:
    """The first day kept by a load made on `load_day`; the one place the boundary is computed."""
    return load_day - timedelta(days=window_days)


def record(con: db.Connection, load_day: date, window_days: int) -> None:
    """Write the boundary a finished load pruned at (inside the load's own transaction)."""
    db.set_meta(con, START_KEY, window_start(load_day, window_days).isoformat())


def of(con: db.Connection, window_days: int) -> Coverage | None:
    """The span the store covers, or None for a store that never finished a load. A store loaded
    before the boundary was recorded gets it from the last load date and `window_days`."""
    finished = db.get_meta(con, "last_success")
    if finished is None:
        return None
    end = datetime.fromisoformat(finished).date()
    recorded = db.get_meta(con, START_KEY)
    start = date.fromisoformat(recorded) if recorded else window_start(end, window_days)
    return Coverage(start, end)
