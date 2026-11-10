"""The investigator: a tool loop of at most `max_calls` model calls that ends in a finding.

The model writes only the summary and names the tool calls it relies on. The map points and the
90-day series come from the store through code, and the weather and events on the finding come
from the context clients, whatever the model asked for. A run that ends without an accepted
submission still returns a finding (counts only, no evidence) so the spike is not lost; its
summary says the investigation did not finish.
"""

import json
import re
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from pothole.analyst import events as events_client
from pothole.analyst import weather as weather_client
from pothole.analyst.detect import SENT_ON, Candidate
from pothole.analyst.finding import AnalystDay, Event, Evidence, Finding, Weather
from pothole.analyst.prompt import (
    CALLS_LEFT,
    LAST_CALL,
    NUDGE_LENGTH,
    NUDGE_NO_TOOL,
    SUBMIT_NEXT,
    SYSTEM_PROMPT,
    TOOLS,
)
from pothole.analyst.tools import Toolbox
from pothole.analyst.wording import causal_phrase
from pothole.budget.limits import DEFAULT_LIMITS, CallLimits
from pothole.llm.client import CallFailed, CallResult, InputTooLarge, LLMClient
from pothole.llm.tokens import count_input_tokens
from pothole.logs import get_logger
from pothole.repair.tools import cap
from pothole.repair.validate import ToolError

MAX_CALLS = 5
FORCE_WHEN_LEFT = 2
FORCE: dict[str, Any] = {"type": "function", "function": {"name": "submit_finding"}}
MAX_SUMMARY = 600
EXCERPT = 300
TRIMMED = 240
UNFINISHED = "The investigation did not finish; only the counts are shown."

log = get_logger(__name__)
WeatherFor = Callable[[date], Weather]
EventsFor = Callable[[str, int, date], list[Event]]
STOPPED = "A source failed during the investigation: the other candidates were counted only"
SourceFailed = Callable[[str, Exception], None]


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def finding_id(c: Candidate) -> str:
    return f"{c.day.isoformat()}-{slug(c.borough)}-{c.board}-{slug(c.complaint_type)}"


def _brief(c: Candidate) -> str:
    return (
        f"Date {c.day.isoformat()} ({c.day.strftime('%A')}). {c.complaint_type} in "
        f"{c.borough} community district {c.board}: {c.observed} requests, against about "
        f"{c.expected:.1f} usual for that weekday ({c.ratio:.1f} times)."
    )


class _Run:
    def __init__(
        self, c: Candidate, box: Toolbox, client: LLMClient, limits: CallLimits, max_calls: int
    ) -> None:
        self.c, self.box, self.client, self.limits, self.max_calls = (
            c,
            box,
            client,
            limits,
            max_calls,
        )
        self.calls = 0
        self.cost = Decimal(0)
        self.trace: list[dict[str, Any]] = []
        self.summary: str | None = None
        self.evidence: list[Evidence] = []
        self.reason = f"no valid submission in {max_calls} calls"
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _brief(c)},
        ]

    def _fit(self, force: Any) -> bool:
        for m in self.messages:
            if count_input_tokens(self.messages, TOOLS, force) <= self.limits.input_tokens:
                return True
            if m.get("role") == "tool" and len(m["content"]) > TRIMMED:
                m["content"] = m["content"][:TRIMMED] + "…[trimmed]"
        return count_input_tokens(self.messages, TOOLS, force) <= self.limits.input_tokens

    def _left(self) -> str:
        n = self.max_calls - self.calls
        if n <= 1:
            return LAST_CALL
        return SUBMIT_NEXT if n == FORCE_WHEN_LEFT else CALLS_LEFT.format(n=n)

    def run(self) -> None:
        while self.calls < self.max_calls and self.summary is None:
            force = FORCE if self.max_calls - self.calls <= FORCE_WHEN_LEFT else None
            if not self._fit(force):
                self.reason = "the conversation no longer fits the input limit"
                return
            sent = [dict(m) for m in self.messages]
            try:
                res = self.client.call(self.messages, TOOLS, self.limits, force)
            except InputTooLarge as exc:
                self.reason = f"call not made: {exc}"
                return
            except CallFailed as exc:
                self.cost += exc.usage.cost
                self.trace.append(
                    {"call": self.calls + 1, "error": str(exc), "cost": str(exc.usage.cost)}
                )
                self.reason = f"the model call failed: {exc}"
                return
            self.calls += 1
            self.cost += res.cost
            self._handle(res, self._record(sent, res))

    def _record(self, sent: list[dict[str, Any]], res: CallResult) -> dict[str, Any]:
        step: dict[str, Any] = {
            "call": self.calls,
            "messages": [
                {**m, "content": cap(m["content"])} if m.get("role") == "tool" else m for m in sent
            ],
            "reply": res.message,
            "finish_reason": res.finish_reason,
            "usage": {"input": res.usage.input_tokens, "output": res.usage.output_tokens},
            "cost": str(res.cost),
            "tool_results": [],
        }
        self.trace.append(step)
        return step

    def _handle(self, res: CallResult, step: dict[str, Any]) -> None:
        if res.finish_reason in ("content_filter", "no_choices"):
            self.reason = f"the model returned {res.finish_reason}"
            self.calls = self.max_calls
            return
        calls = res.message.get("tool_calls") or []
        if res.finish_reason == "length" or not calls:
            if res.finish_reason != "length":
                self.messages.append(
                    {"role": "assistant", "content": res.message.get("content") or ""}
                )
            nudge = NUDGE_LENGTH if res.finish_reason == "length" else NUDGE_NO_TOOL
            self.messages.append({"role": "user", "content": f"{nudge} {self._left()}"})
            return
        self.messages.append(res.message)
        for call in calls:
            name = str(call.get("function", {}).get("name"))
            if self.summary is not None or (
                name == "submit_finding"
                and any(t["name"] == "submit_finding" for t in step["tool_results"])
            ):
                content = "ignored: one submission per reply"
            else:
                content = self._run_tool(name, call)
            self.messages.append({"role": "tool", "tool_call_id": call["id"], "content": content})
            step["tool_results"].append({"name": name, "content": cap(content)})
        if self.summary is None:
            self.messages[-1]["content"] += "\n" + self._left()

    def _run_tool(self, name: str, call: dict[str, Any]) -> str:
        try:
            args = json.loads(call["function"].get("arguments") or "{}")
            if not isinstance(args, dict):
                raise ToolError("arguments must be a JSON object")
            if name == "submit_finding":
                self._submit(args)
                return "accepted"
            return self.box.run(name, args, call["id"])
        except json.JSONDecodeError as exc:
            return f"error: bad arguments: {exc}"
        except ToolError as exc:
            return f"error: {exc}"

    def _submit(self, args: dict[str, Any]) -> None:
        summary, refs = args.get("summary"), args.get("evidence")
        if not isinstance(summary, str) or not summary.strip():
            raise ToolError("summary is required")
        if len(summary) > MAX_SUMMARY:
            raise ToolError(f"summary is {len(summary)} characters; the limit is {MAX_SUMMARY}")
        hit = causal_phrase(summary)
        if hit:
            raise ToolError(f"the summary says {hit!r}; say what the spike is associated with")
        if args.get("wording_check") is not True:
            raise ToolError("wording_check must be true once you have re-read the summary")
        if not isinstance(refs, list) or not refs or not all(isinstance(r, str) for r in refs):
            raise ToolError("evidence must list the tool_call_id of at least one tool call made")
        unknown = [r for r in refs if r not in self.box.calls]
        if unknown:
            raise ToolError(f"evidence names tool calls that were not made: {', '.join(unknown)}")
        self.evidence = [
            Evidence(t.name, t.args, cap(t.result, EXCERPT))
            for t in (self.box.calls[r] for r in dict.fromkeys(refs))
        ]
        self.summary = summary.strip()


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
        state = _Run(candidate, box, client, limits, max_calls)
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
            summary=state.summary or UNFINISHED,
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
            report(label, exc)
            raise

    return call


def _part[T](notes: list[str], label: str, get: Callable[[], T], default: T) -> T:
    """One part of the finding. Whatever fails leaves its default and a note: the model calls
    are already paid for and the trace must not be lost."""
    try:
        return get()
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
        findings.append(run(c, db, client, **kwargs))
    return AnalystDay(analysed, tested, cleared, findings)
