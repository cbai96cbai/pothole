"""What the analyst publishes for a day: the contract shared by the investigator, the daily run
and the site."""

from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Literal

WeatherSource = Literal["archive", "forecast_past_days"]


@dataclass(frozen=True)
class Weather:
    precip_mm: float | None
    tmax_c: float | None
    source: WeatherSource


@dataclass(frozen=True)
class Event:
    name: str
    type: str
    start: str
    end: str


@dataclass(frozen=True)
class Evidence:
    """One tool call the summary relies on, with a short excerpt of its result."""

    tool: str
    args: dict[str, Any]
    excerpt: str


@dataclass(frozen=True)
class Finding:
    id: str
    day: date
    borough: str
    board: int
    complaint_type: str
    observed: int
    expected: float
    ratio: float
    q: float
    summary: str
    evidence: list[Evidence]
    series: list[tuple[date, int]]
    points: list[tuple[float, float]]
    weather: Weather | None
    events: list[Event]
    trace: list[dict[str, Any]] = field(default_factory=list)
    cost: Decimal = Decimal("0")


@dataclass(frozen=True)
class AnalystDay:
    """One analysed day: how many series were tested, how many cleared the bar, and the
    findings for the (at most two) that were investigated. An empty list is a quiet day."""

    day: date
    tested: int
    cleared: int
    findings: list[Finding]
    skipped_reason: str | None = None


def to_json(value: Any) -> Any:
    """Dataclasses, dates and Decimals as JSON-ready values."""
    if hasattr(value, "__dataclass_fields__"):
        return to_json(asdict(value))
    if isinstance(value, dict):
        return {k: to_json(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [to_json(v) for v in value]
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value
