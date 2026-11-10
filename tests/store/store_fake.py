"""An in-memory stand-in for the Socrata client that answers the store's queries."""

import re
from collections import Counter
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import Any

from pothole.socrata.client import Socrata, SourceUnavailable

NOW = datetime(2026, 11, 10, 12, 0, 0)
BASE = datetime(2026, 11, 9, 0, 0, 0)  # an updated_at clock, UTC


def stamp(minutes: float) -> str:
    t = BASE + timedelta(minutes=minutes)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def make_row(
    i: int, minutes: float, created: str = "2026-11-08T10:00:00.000", **over: str
) -> dict[str, str]:
    row = {
        "unique_key": str(1000 + i),
        ":id": f"row-{i:05d}",
        ":updated_at": stamp(minutes),
        ":created_at": stamp(minutes - 1),
        "created_date": created,
        "agency": "DOT",
        "complaint_type": "Street Condition",
        "status": "Open",
        "community_board": "02 BROOKLYN",
        "borough": "BROOKLYN",
        "latitude": "40.6",
        "longitude": "-73.9",
    }
    row.update(over)
    return row


class FakeSocrata(Socrata):
    def __init__(self, rows: list[dict[str, str]]) -> None:
        self.rows = rows
        self.queries: list[dict[str, str]] = []
        self.before_query: Callable[[int, Mapping[str, str]], None] | None = None

    def _matching(self, where: str) -> list[dict[str, str]]:
        out = list(self.rows)
        if m := re.search(r"created_date >= '([^']*)'", where):
            out = [r for r in out if r["created_date"] >= m.group(1)]
        if m := re.search(r"created_date < '([^']*)'", where):
            out = [r for r in out if r["created_date"] < m.group(1)]
        if m := re.search(r":updated_at <= '([^']*)'", where):
            out = [r for r in out if r[":updated_at"] <= m.group(1)]
        if m := re.search(r"^:updated_at >= '([^']*)'", where):
            out = [r for r in out if r[":updated_at"] >= m.group(1)]
        if m := re.search(
            r"\(\(:updated_at > '([^']*)'\) OR \(:updated_at = '[^']*' AND :id > '([^']*)'\)\)",
            where,
        ):
            t, i = m.groups()
            out = [r for r in out if (r[":updated_at"], r[":id"]) > (t, i)]
        if m := re.search(r"unique_key in \(([^)]*)\)", where):
            keys = set(re.findall(r"'([^']*)'", m.group(1)))
            out = [r for r in out if r["unique_key"] in keys]
        return out

    def query(self, dataset: str, params: Mapping[str, str]) -> list[dict[str, Any]]:
        if self.before_query:
            self.before_query(len(self.queries), params)
        self.queries.append(dict(params))
        select = params.get("$select", "")
        rows = self._matching(params.get("$where", ""))
        if "max(:updated_at)" in select:
            return [{"latest": max(r[":updated_at"] for r in rows)}] if rows else []
        if params.get("$group"):
            days = Counter(r["created_date"][:10] for r in rows)
            return [{"d": f"{d}T00:00:00.000", "n": str(n)} for d, n in sorted(days.items())]
        order = params.get("$order", "")
        if order == ":updated_at,:id":
            rows.sort(key=lambda r: (r[":updated_at"], r[":id"]))
        elif order:
            rows.sort(key=lambda r: r[order])
        offset = int(params.get("$offset", "0"))
        rows = rows[offset : offset + int(params["$limit"])]
        if select == "unique_key":
            return [{"unique_key": r["unique_key"]} for r in rows]
        return [dict(r) for r in rows]


def outage_on(n: int) -> Callable[[int, Mapping[str, str]], None]:
    def hook(count: int, params: Mapping[str, str]) -> None:
        if count == n:
            raise SourceUnavailable("fake outage")

    return hook
