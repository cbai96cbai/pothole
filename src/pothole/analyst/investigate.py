"""The investigator: a tool loop of at most `max_calls` model calls that ends in a finding.

The model writes only the summary and cites by number the tool calls it relies on. The map
points and the 90-day series come from the store through code, and the weather and events on
the finding come from the context clients, whatever the model asked for. A run that ends
without an accepted submission still returns a finding (counts only, no evidence, an empty
summary) so the spike is not lost; the page alone says the investigation did not finish.
"""

import re
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from pothole.analyst import events as events_client
from pothole.analyst import weather as weather_client
from pothole.analyst.detect import SENT_ON, Candidate
from pothole.analyst.finding import AnalystDay, Event, Finding, Weather
from pothole.analyst.loop import Run
from pothole.analyst.tools import ReportFailed, Toolbox
from pothole.budget.limits import DEFAULT_LIMITS, CallLimits
from pothole.llm.client import LLMClient
from pothole.logs import get_logger

MAX_CALLS = 5
log = get_logger(__name__)
WeatherFor = Callable[[date], Weather]
EventsFor = Callable[[str, int, date], list[Event]]
STOPPED = "A source failed during the investigation: the other candidates were counted only"
SourceFailed = Callable[[str, Exception], None]


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def finding_id(c: Candidate) -> str:
    return f"{c.day.isoformat()}-{slug(c.borough)}-{c.board}-{slug(c.complaint_type)}"


def run(
    candidate: Candidate,
    db: Path,
    client: LLMClient,
    *,
    limits: CallLimits = DEFAULT_LIMITS,
    max_calls: int = MAX_CALLS,
    weather_for: WeatherFor | None = None,
    events_for: EventsFor | None = None,
    on_source_failure: SourceFailed | None = None,
    stop: Callable[[], bool] | None = None,
) -> Finding:
    """Investigate one candidate against the store at `db`. `on_source_failure(label, exc)` is
    told of every failed weather or events lookup before the failure goes on."""
    box = Toolbox(
        db,
        candidate,
        _watched("weather", weather_for or weather_client.daily, on_source_failure),
        _watched(
            "events", events_for or (lambda b, n, d: events_client.on(b, n, d)), on_source_failure
        ),
    )
    try:
        state = Run(candidate, box, client, limits, max_calls, stop)
        state.run()
        notes: list[str] = []
        if state.summary is None:
            notes.append(f"unfinished: {state.reason}")
        series: list[tuple[date, int]] = _part(notes, "series", box.series_rows, [])
        points: list[tuple[float, float]] = _part(notes, "points", box.points, [])
        weather: Weather | None = _part(notes, "weather", box.weather, None)
        found: list[Event] = _part(notes, "events", box.events, [])
        if getattr(found, "truncated", False):
            notes.append("events: the query hit its row limit; the list may be incomplete")
        return Finding(
            id=finding_id(candidate),
            day=candidate.day,
            borough=candidate.borough,
            board=candidate.board,
            complaint_type=candidate.complaint_type,
            observed=candidate.observed,
            expected=candidate.expected,
            ratio=candidate.ratio,
            q=candidate.q,
            summary=state.summary or "",
            evidence=state.evidence,
            series=series,
            points=points,
            weather=weather,
            events=list(found),
            status="investigated" if state.summary else "unfinished",
            trace=state.trace,
            cost=state.cost,
            notes=notes,
        )
    finally:
        box.close()


def _watched[**P, R](label: str, fn: Callable[P, R], report: SourceFailed | None) -> Callable[P, R]:
    if report is None:
        return fn

    def call(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            try:
                report(label, exc)
            except Exception as failure:
                raise ReportFailed(
                    f"the report of the {label} failure failed: {type(failure).__name__}: {failure}"
                ) from failure
            raise

    return call


def _part[T](notes: list[str], label: str, get: Callable[[], T], default: T) -> T:
    """One part of the finding. Whatever fails leaves its default and a note: the model calls
    are already paid for and the trace must not be lost."""
    try:
        return get()
    except ReportFailed:
        raise
    except Exception as exc:
        log.warning(f"{label} unavailable", extra={"error": str(exc)})
        notes.append(f"{label} unavailable: {type(exc).__name__}: {exc}")
        return default


def day(
    db: Path,
    candidates: list[Candidate],
    client: LLMClient,
    *,
    analysed: date,
    tested: int,
    cleared: int,
    stop: Callable[[], bool] | None = None,
    **kwargs: Any,
) -> AnalystDay:
    """Investigate the top candidates by ratio. No candidates: no findings and no model call.
    When `stop()` is true after a candidate, the rest are counted, not investigated, and the day
    says so."""
    top = sorted(candidates, key=lambda c: (-c.ratio, c.borough, c.board, c.complaint_type))
    findings: list[Finding] = []
    for c in top[:SENT_ON]:
        if findings and stop is not None and stop():
            return AnalystDay(analysed, tested, cleared, findings, skipped_reason=STOPPED)
        findings.append(run(c, db, client, stop=stop, **kwargs))
    return AnalystDay(analysed, tested, cleared, findings)
