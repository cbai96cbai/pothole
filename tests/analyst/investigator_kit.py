"""Shared pieces for the investigator tests: a small store, a scripted model, canned context."""

import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from pothole.analyst.detect import Candidate
from pothole.analyst.finding import Event, Weather
from pothole.budget.limits import CallLimits
from pothole.llm.client import CallResult, Usage
from pothole.spec.model import PipelineSpec
from pothole.store import db

SPEC = PipelineSpec.load(Path(__file__).parents[2] / "fixtures" / "batches" / "spec.yaml")
DAY = date(2026, 11, 7)
TYPE = "HEAT/HOT WATER"


def row(i: int, day: date, board: str = "03 BROOKLYN", ctype: str = TYPE, **kw: Any) -> dict:
    out = {
        "unique_key": f"{day:%Y%m%d}-{board[:2]}-{ctype[:3]}-{i:05d}",
        "created_date": f"{day.isoformat()}T{(i % 24):02d}:15:00.000",
        "agency": "HPD",
        "complaint_type": ctype,
        "descriptor": "ENTIRE BUILDING" if i % 3 else "APARTMENT ONLY",
        "location_type": "RESIDENTIAL BUILDING",
        "incident_zip": "11216",
        "status": "Open",
        "community_board": board,
        "borough": board.split(" ", 1)[1].title(),
        "latitude": str(40.68 + (i % 50) / 1000),
        "longitude": str(-73.95 - (i % 40) / 1000),
    }
    return {**out, **kw}


def make_store(path: Path, spike: int = 30) -> Path:
    """30 days of 3 requests a day, `spike` on DAY, and some in two other districts."""
    con = db.open(path)
    db.ensure_raw(con, SPEC)
    rows = [row(i, DAY - timedelta(days=d)) for d in range(1, 30) for i in range(3)]
    rows += [row(i, DAY) for i in range(spike)]
    rows += [row(i, DAY, "05 QUEENS") for i in range(8)]
    rows += [row(i, DAY, "12 MANHATTAN") for i in range(4)]
    rows += [row(i, DAY, "03 BROOKLYN", "NOISE - STREET/SIDEWALK") for i in range(5)]
    db.upsert(con, SPEC, rows, datetime(2026, 11, 9, 6))
    con.close()
    return path


def candidate(
    ratio: float = 10.0, board: int = 3, ctype: str = TYPE, observed: int = 30
) -> Candidate:
    return Candidate("Brooklyn", board, ctype, DAY, observed, 3.0, ratio, 1e-9, 1e-7)


def weather_for(_: date) -> Weather:
    return Weather(0.0, 3.1, "archive")


def events_for(borough: str, board: int, day: date) -> list[Event]:
    return [Event("Street fair", "Street Event", f"{day}T10:00:00.000", f"{day}T18:00:00.000")]


def tool_call(name: str, args: dict[str, Any], id_: str) -> dict[str, Any]:
    return {
        "id": id_,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


def reply(*calls: dict[str, Any]) -> CallResult:
    message = {"role": "assistant", "content": None, "tool_calls": list(calls)}
    return CallResult(message, Usage(1000, 100, Decimal("0.001")), "tool_calls", 0.0)


class Scripted:
    def __init__(self, script: list[CallResult | Exception]) -> None:
        self.script = list(script)
        self.sent: list[dict[str, Any]] = []

    def call(
        self,
        messages: Any,
        tools: Any = None,
        limits: CallLimits | None = None,
        tool_choice: Any = None,
    ) -> CallResult:
        self.sent.append({"messages": [dict(m) for m in messages], "tool_choice": tool_choice})
        nxt = self.script.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


def submit(summary: str, refs: list[Any], check: bool = True) -> dict[str, Any]:
    return tool_call(
        "submit_finding", {"summary": summary, "evidence": refs, "wording_check": check}, "s1"
    )
