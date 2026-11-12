"""The investigator's tools: fixed, parameterised queries over the store, plus the two context
lookups. The model chooses a tool and its arguments; no model-written SQL ever runs.

The store is opened read-only with external access off, extension autoloading off and the
configuration locked; every statement is cut off after 2 seconds by a watchdog thread.
"""

import json
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import duckdb

from pothole.analyst.detect import Candidate
from pothole.analyst.finding import Event, Weather
from pothole.repair.validate import ToolError
from pothole.spec.normalise import board

STATEMENT_TIMEOUT_S = 2.0
MAX_DAYS = 90
MAX_TOP = 10
MAX_POINTS = 500
MIN_PRECISION = 5  # decimal places kept for map points

_WHERE = "upper(trim(community_board)) IN (?, ?) AND complaint_type = ?"
_DAY = "CAST(created_date AS DATE)"
SERIES_SQL = (
    f"SELECT {_DAY}, count(*) FROM raw WHERE {_WHERE} AND {_DAY} BETWEEN ? AND ? GROUP BY 1"
)
POINTS_SQL = (
    f"SELECT latitude, longitude FROM raw WHERE {_WHERE} AND {_DAY} = ? "
    "AND latitude IS NOT NULL AND longitude IS NOT NULL ORDER BY unique_key"
)
NEIGHBOURS_SQL = (
    f"SELECT upper(trim(community_board)), count(*) FROM raw WHERE complaint_type = ? "
    f"AND {_DAY} = ? AND community_board IS NOT NULL GROUP BY 1"
)
_BREAKDOWN = (
    "SELECT coalesce(CAST({expr} AS VARCHAR), '(blank)'), count(*) AS n FROM raw "
    f"WHERE {_WHERE} AND {_DAY} = ? GROUP BY 1 ORDER BY n DESC, 1 LIMIT ?"
)
BREAKDOWN_SQL = {
    name: _BREAKDOWN.format(expr=expr)
    for name, expr in {
        "descriptor": "descriptor",
        "incident_zip": "incident_zip",
        "hour": "hour(created_date)",
        "agency": "agency",
        "location_type": "location_type",
        "status": "status",
    }.items()
}
BREAKDOWN_BY = tuple(BREAKDOWN_SQL)


def open_locked(path: Path) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(
        str(path),
        read_only=True,
        config={
            "enable_external_access": "false",
            "autoload_known_extensions": "false",
            "autoinstall_known_extensions": "false",
            "lock_configuration": "true",
        },
    )


@dataclass(frozen=True)
class ToolCall:
    name: str
    args: dict[str, Any]
    result: str


def _int(args: dict[str, Any], key: str, default: int, high: int) -> int:
    value = args.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= high:
        raise ToolError(f"{key} must be an integer from 1 to {high}")
    return value


class Toolbox:
    def __init__(
        self,
        db: Path,
        candidate: Candidate,
        weather_for: Callable[[date], Weather],
        events_for: Callable[[str, int, date], list[Event]],
    ) -> None:
        self.con = open_locked(db)
        self.c = candidate
        self._weather_for, self._events_for = weather_for, events_for
        self.calls: dict[int, ToolCall] = {}  # ref number -> the successful call, for evidence
        self._context: dict[str, Any] = {}
        name = candidate.borough.upper()
        self._district = (f"{candidate.board:02d} {name}", f"{candidate.board} {name}")

    def close(self) -> None:
        self.con.close()

    def _query(self, sql: str, params: list[Any]) -> list[tuple[Any, ...]]:
        watchdog = threading.Timer(STATEMENT_TIMEOUT_S, self.con.interrupt)
        watchdog.start()
        try:
            return self.con.execute(sql, params).fetchall()
        except duckdb.InterruptException as exc:
            raise ToolError(f"the query took longer than {STATEMENT_TIMEOUT_S:g} seconds") from exc
        finally:
            watchdog.cancel()

    def _scope(self) -> list[Any]:
        return [*self._district, self.c.complaint_type]

    def series_rows(self, days: int = MAX_DAYS) -> list[tuple[date, int]]:
        """Daily counts for this district and type, the `days` ending on the spike day, zeros
        included."""
        first = self.c.day - timedelta(days=days - 1)
        found = {d: int(n) for d, n in self._query(SERIES_SQL, [*self._scope(), first, self.c.day])}
        return [
            (first + timedelta(days=i), found.get(first + timedelta(days=i), 0))
            for i in range(days)
        ]

    def points(self) -> list[tuple[float, float]]:
        """At most 500 coordinates of the spike day's rows, an even deterministic sample."""
        rows = self._query(POINTS_SQL, [*self._scope(), self.c.day])
        if len(rows) > MAX_POINTS:
            rows = [rows[i * len(rows) // MAX_POINTS] for i in range(MAX_POINTS)]
        return [(round(float(a), MIN_PRECISION), round(float(b), MIN_PRECISION)) for a, b in rows]

    def weather(self) -> Weather:
        if "weather" not in self._context:
            self._context["weather"] = self._weather_for(self.c.day)
        weather: Weather = self._context["weather"]
        return weather

    def events(self) -> list[Event]:
        if "events" not in self._context:
            self._context["events"] = self._events_for(self.c.borough, self.c.board, self.c.day)
        found: list[Event] = self._context["events"]
        return found

    def run(self, name: str, args: dict[str, Any], ref: int) -> str:
        """Run one tool. A success is kept under `ref`; a failure raises and keeps nothing."""
        if name == "series":
            days = _int(args, "days", 30, MAX_DAYS)
            result = json.dumps([[d.isoformat(), n] for d, n in self.series_rows(days)])
        elif name == "breakdown":
            result = self._breakdown(args)
        elif name == "neighbours":
            result = self._neighbours()
        elif name == "weather":
            result = self._context_result(self.weather)
        elif name == "events":
            result = self._context_result(lambda: [vars(e) for e in self.events()])
        else:
            raise ToolError(f"unknown tool {name!r}")
        self.calls[ref] = ToolCall(name, args, result)
        return result

    @staticmethod
    def _context_result(get: Callable[[], Any]) -> str:
        try:
            value = get()
        except Exception as exc:  # the lookup failed: the model is told, the run goes on
            raise ToolError(f"unavailable: {type(exc).__name__}: {exc}") from exc
        return json.dumps(vars(value) if hasattr(value, "__dict__") else value)

    def _breakdown(self, args: dict[str, Any]) -> str:
        by = args.get("by")
        if by not in BREAKDOWN_SQL:
            raise ToolError(f"by must be one of {', '.join(BREAKDOWN_BY)}")
        top = _int(args, "top", 5, MAX_TOP)
        rows = self._query(BREAKDOWN_SQL[by], [*self._scope(), self.c.day, top])
        return json.dumps([[v, int(n)] for v, n in rows])

    def _neighbours(self) -> str:
        rows = self._query(NEIGHBOURS_SQL, [self.c.complaint_type, self.c.day])
        own = (self.c.borough, self.c.board)
        others: list[tuple[str, int, int]] = []
        for raw, n in rows:
            district = board(raw)
            if district is not None and district != own:
                others.append((district[0], district[1], int(n)))
        others.sort(key=lambda t: (-t[2], t[0], t[1]))
        return json.dumps(
            {
                "this_district": self.c.observed,
                "other_districts": [
                    {"borough": b, "board": k, "count": n} for b, k, n in others[:MAX_TOP]
                ],
            }
        )
