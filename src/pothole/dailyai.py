"""The daily run's model-using steps: the real repair, the Sunday drill and the investigations.

Every step runs under a wall-clock timeout: a call that hangs is recorded as a failed step, charged
what the step could at most have cost, and the run goes on. The abandoned call runs on in a daemon
thread that cannot hold the process open.
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol

from pothole.analyst import investigate
from pothole.analyst.detect import Candidate
from pothole.analyst.finding import AnalystDay
from pothole.budget.envelope import Step
from pothole.budget.limits import DEFAULT_LIMITS, call_max_cost
from pothole.drills import catalog, judge
from pothole.llm.client import LLMClient
from pothole.logs import get_logger
from pothole.repair import agent
from pothole.repair.decide import decide
from pothole.repair.outcome import RepairOutcome
from pothole.repair.sink import Filed
from pothole.runrecord import RepairRecord
from pothole.spec import engine, ops
from pothole.spec.checks import CheckResult
from pothole.spec.model import PipelineSpec

log = get_logger(__name__)
REPAIR_TIMEOUT_S = 600.0
INVESTIGATE_TIMEOUT_S = 900.0
TIMED_OUT = "the investigation timed out; only the counts are shown"


class StepTimeout(Exception):
    """A step did not finish inside its wall-clock allowance."""


class Sink(Protocol):
    def open_pr(
        self,
        outcome: RepairOutcome,
        spec: PipelineSpec,
        before: list[CheckResult],
        after: list[CheckResult],
        *,
        day: date,
        drill: bool = False,
        verdict: str | None = None,
    ) -> Filed: ...

    def open_issue(
        self, reason: str, failing: list[CheckResult], *, day: date, drill: bool = False
    ) -> Filed: ...


@dataclass
class Spend:
    """What the run's model steps cost, charged at a step's maximum when it did not report."""

    total: Decimal = Decimal(0)

    def add(self, amount: Decimal) -> None:
        self.total += amount


def with_timeout[T](fn: Callable[[], T], seconds: float) -> T:
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["value"] = fn()
        except BaseException as exc:  # carried to the caller's thread
            box["error"] = exc

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(seconds)
    if worker.is_alive():
        raise StepTimeout(f"no answer after {seconds:g} s")
    if "error" in box:
        raise box["error"]
    return box["value"]  # type: ignore[no-any-return]


def slot_max(step: Step) -> Decimal:
    return step.calls * call_max_cost(DEFAULT_LIMITS)


def _file(
    sink: Sink,
    outcome: RepairOutcome,
    spec: PipelineSpec,
    before: list[CheckResult],
    after: list[CheckResult],
    day: date,
    drill: bool,
    verdict: str | None,
) -> RepairRecord:
    """Send a decision to the sink. A drill's pull request carries the judge's verdict or is
    refused here: the drill's point is the verdict."""
    if drill and not verdict:
        raise ValueError("a drill is filed only with the judge's verdict")
    by = outcome.decided_by
    if outcome.action == "patch":
        filed = sink.open_pr(outcome, spec, before, after, day=day, drill=drill, verdict=verdict)
        if filed.kind != "pr":  # gates or pull request creation failed: only an issue exists
            return RepairRecord("issue", drill, filed.ref, verdict, False, by)
        return RepairRecord("patch", drill, filed.ref, verdict, not drill, by)
    if outcome.action == "issue":
        filed = sink.open_issue(outcome.reason or "no reason given", before, day=day, drill=drill)
        return RepairRecord("issue", drill, filed.ref, verdict, False, by)
    return RepairRecord("no_change", drill, None, verdict, False, by)


def _failed_step(
    sink: Sink, failing: list[CheckResult], day: date, drill: bool, what: str, exc: Exception
) -> RepairRecord:
    log.error(f"{what} failed", extra={"error": f"{type(exc).__name__}: {exc}"})
    reason = f"The {what} did not finish ({type(exc).__name__}: {exc}); a person should look."
    filed = sink.open_issue(reason, failing, day=day, drill=drill)
    verdict = f"no verdict: the {what} did not finish" if drill else None
    return RepairRecord("issue", drill, filed.ref, verdict, False, "rule")


def file_rule(
    sink: Sink, outcome: RepairOutcome, spec: PipelineSpec, failing: list[CheckResult], day: date
) -> RepairRecord:
    """File a decision the volume rule made on a real failure; no model was involved."""
    return _file(sink, outcome, spec, failing, [], day, False, None)


def real_repair(
    sink: Sink,
    client: LLMClient,
    spec: PipelineSpec,
    rows: list[dict[str, str]],
    failing: list[CheckResult],
    fields: dict[str, str],
    now: datetime,
    spend: Spend,
    step: Step,
    timeout_s: float = REPAIR_TIMEOUT_S,
) -> RepairRecord:
    """The repair agent on a real failure; its proposal is filed unjudged."""
    day = now.date()
    paid = False
    try:
        outcome = with_timeout(
            lambda: decide(
                lambda sp, f, r, fl: agent.repair(sp, r, f, fl, client, now=now),
                spec,
                failing,
                rows,
                fields,
            ),
            timeout_s,
        )
        spend.add(outcome.cost)
        paid = True
        after: list[CheckResult] = []
        if outcome.action == "patch":
            after = engine.run(ops.apply(spec, outcome.ops), rows, [], now).checks
        return _file(sink, outcome, spec, failing, after, day, False, None)
    except Exception as exc:
        if not paid:  # a step that never reported its cost is charged its maximum
            spend.add(slot_max(step))
        return _failed_step(sink, failing, day, False, "repair", exc)


def pick_case(day: date) -> catalog.Case:
    """One dev case, rotating by week so successive Sundays use different faults."""
    pool = [c for c in catalog.load() if c.split == "dev" and c.expected_action != "nothing"]
    return pool[(day.toordinal() // 7) % len(pool)]


def drill(
    sink: Sink,
    client: LLMClient,
    day: date,
    spend: Spend,
    step: Step,
    timeout_s: float = REPAIR_TIMEOUT_S,
) -> RepairRecord:
    """One planted fault from the catalog's dev cases: repaired, judged, filed with the verdict."""
    failing: list[CheckResult] = []
    paid = False
    try:
        case = pick_case(day)
        spec = catalog.load_spec()
        rows, fields = catalog.seen_batch(case)
        now = datetime.strptime(case.batch_seen, "%Y-%m-%d") + timedelta(days=1, hours=30)
        checks = catalog.run_engine(spec, rows, case.batch_seen).checks
        failing = [c for c in checks if c.level == "fail"]
        strings: list[dict[str, str]] = [{k: str(v) for k, v in r.items()} for r in rows]
        outcome = with_timeout(
            lambda: decide(
                lambda sp, f, r, fl: agent.repair(
                    sp, r, f, fl, client, now=now, history=catalog.history()
                ),
                spec,
                failing,
                strings,
                fields,
            ),
            timeout_s,
        )
        spend.add(outcome.cost)
        paid = True
        verdict = judge.judge(case, outcome, spec)
        text = (
            f"{'correct' if verdict.correct else 'not correct'} "
            f"(decided by {outcome.decided_by}): {verdict.reason}"
        )
        after: list[CheckResult] = []
        if outcome.action == "patch":
            after = engine.run(ops.apply(spec, outcome.ops), strings, [], now).checks
        return _file(sink, outcome, spec, failing, after, day, True, text)
    except Exception as exc:
        if not paid:
            spend.add(slot_max(step))
        return _failed_step(sink, failing, day, True, "drill", exc)


def investigate_day(
    store: Any,
    candidates: list[Candidate],
    client: LLMClient,
    day: date,
    tested: int,
    cleared: int,
    spend: Spend,
    steps: tuple[Step, Step],
    timeout_s: float = INVESTIGATE_TIMEOUT_S,
    **kwargs: Any,
) -> AnalystDay:
    """The investigator on the day's top candidates. A step that fails or times out leaves the
    day with its counts and says so."""
    try:
        result = with_timeout(
            lambda: investigate.day(
                store, candidates, client, analysed=day, tested=tested, cleared=cleared, **kwargs
            ),
            timeout_s,
        )
    except Exception as exc:
        spend.add(sum((slot_max(s) for s in steps), Decimal(0)))
        log.error("investigation failed", extra={"error": f"{type(exc).__name__}: {exc}"})
        reason = TIMED_OUT if isinstance(exc, StepTimeout) else f"the investigation failed: {exc}"
        return AnalystDay(day, tested, cleared, [], skipped_reason=reason)
    spend.add(sum((f.cost for f in result.findings), Decimal(0)))
    return result
