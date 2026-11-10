"""How late rows arrive: the share of each created day's rows that were in the store within k days.

`_created_at` is the source's first-arrival time, but a full reload resets it for every row, so
the reload time is taken as the most common `_created_at` in the window and only rows filed after
it are measured. Lag is counted in New York calendar days between the created date and the day
`_created_at` falls on. For lag k only created days more than k days older than the latest arrival
day count, so a recent day is not penalised for hours that have not happened yet; later lags
therefore rest on fewer days, and the rows measured are listed for each lag.
"""

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from pothole.store.db import RAW, Connection

MAX_LAG = 7
DELAY_PATH = Path(__file__).parents[3] / "data" / "delay.json"
_NY = "(_created_at AT TIME ZONE 'UTC' AT TIME ZONE 'America/New_York')"


@dataclass(frozen=True)
class Delay:
    reload_at: datetime | None  # New York time of the reload, None when the store is empty
    rows: int  # rows measured at lag 0: from days at least one day older than the latest arrival
    share_by_lag: dict[int, float]  # k -> share of rows that arrived within k days
    rows_by_lag: dict[int, int]  # k -> rows measured for that lag


def measure(con: Connection) -> Delay:
    top = con.execute(
        f"SELECT {_NY}, count(*) AS n FROM {RAW} WHERE _created_at IS NOT NULL "
        "GROUP BY 1 ORDER BY n DESC, 1 LIMIT 1"
    ).fetchone()
    if top is None:
        return Delay(None, 0, {}, {})
    reload_at: datetime = top[0]
    latest: date = con.execute(f"SELECT CAST(max({_NY}) AS DATE) FROM {RAW}").fetchone()[0]  # type: ignore[index]
    share: dict[int, float] = {}
    measured: dict[int, int] = {}
    total = 0
    for k in range(MAX_LAG + 1):
        within, seen = con.execute(
            "SELECT count(*) FILTER (WHERE lag <= ?), count(*) FROM ("
            f"SELECT CAST(created_date AS DATE) AS made, "
            f"date_diff('day', CAST(created_date AS DATE), CAST({_NY} AS DATE)) AS lag "
            f"FROM {RAW} WHERE _created_at IS NOT NULL AND created_date > ?) "
            "WHERE date_diff('day', made, ?) > ?",
            [k, reload_at, latest, k],
        ).fetchone()  # type: ignore[misc]
        if k == 0:
            total = int(seen)
        if seen:
            share[k] = within / seen
            measured[k] = int(seen)
    return Delay(reload_at, total, share, measured)


def save(delay: Delay, path: Path = DELAY_PATH) -> None:
    body = {
        "label": "share of a day's rows in the store within k days of the day they were filed",
        "reload_at": None if delay.reload_at is None else delay.reload_at.isoformat(),
        "rows_measured_at_lag_0": delay.rows,
        "rows_measured_note": "rows filed after the reload, from days more than k days older "
        "than the latest arrival day; the row count for each lag is below",
        "rows_by_lag_days": {str(k): v for k, v in delay.rows_by_lag.items()},
        "share_by_lag_days": {str(k): round(v, 4) for k, v in delay.share_by_lag.items()},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=2) + "\n")
