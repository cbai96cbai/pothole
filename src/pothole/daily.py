"""One daily run: load, check, repair or drill, analyse, record, publish the site data.

Order: the envelope check comes first, before anything is loaded or paid for. A source
outage or an incomplete load is recorded and filed as an issue; it never calls the model and never
produces new analysis. Checks that fail go to the repair agent (only when AI is enabled), else to an
issue. A Sunday with no failure and no real repair earlier that day runs one judged drill. The
analyst works on the day two days back, only after a complete load (`last_success` is written by
the load itself, after the whole window loaded and the checks ran).

Every run that starts leaves exactly one terminal record per (run id, attempt), appended last: the
record is built, every step runs including the site build (which shows the record as the newest
run), then the record is appended once. Any exception ends in a record with status `error` (the
step, the exception's class and a cut, secret-free message, the cost so far) unless this attempt's
record was already appended; then the exception goes on and the job fails (the envelope refusal
included, which happens before anything is loaded or paid for).

A source failure (reconcile, repair sample, weather or events lookup) bars every later model step.
A source that rejects the load for a missing field is a schema change: diagnosed from a raw sample
and sent to repair, not an outage. A day's analysis is written once and never again.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pyarrow as pa

from pothole import dailyai, dailyrepair, runrecord, sitedata
from pothole.analyst import detect, universe
from pothole.analyst.finding import AnalystDay, to_json
from pothole.analyst.holidays import is_holiday
from pothole.budget.envelope import DEFAULT_STEPS, RunConfig, run_envelope
from pothole.budget.tally import Tally, settle_if_complete
from pothole.dailyai import Sink, Spend
from pothole.dailycounts import day_counts, refresh_counts, write_json, write_vintage
from pothole.dailyerror import Progress, describe
from pothole.llm.client import LLMClient
from pothole.logs import get_logger
from pothole.repair import render
from pothole.repair.routing import route
from pothole.repair.validate import failed
from pothole.runrecord import CheckResult, RunRecord, Status
from pothole.socrata.client import Socrata, SourceRejected, SourceUnavailable
from pothole.spec import checks as spec_checks
from pothole.spec.model import PipelineSpec
from pothole.store import db, load, reconcile

NEW_YORK = ZoneInfo("America/New_York")
SUNDAY = 6
log = get_logger(__name__)


@dataclass
class Env:
    repo: Path
    state: Path
    socrata: Socrata
    sink: Sink
    client: LLMClient | None = None
    ai_enabled: bool = False
    tally: Tally | None = None
    config: RunConfig | None = None
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    attempt: int = 1
    clock: Callable[[], datetime] = lambda: datetime.now(NEW_YORK)
    page_size: int = load.PAGE_SIZE
    site_dir: Path | None = None
    repair_timeout_s: float = dailyai.REPAIR_TIMEOUT_S
    investigate_timeout_s: float = dailyai.INVESTIGATE_TIMEOUT_S
    investigator: dict[str, Any] = field(default_factory=dict)  # weather_for, events_for

    @property
    def data(self) -> Path:
        return self.repo / "data"


def _records(results: list[spec_checks.CheckResult]) -> list[CheckResult]:
    return [CheckResult(r.name, r.level, r.detail) for r in results]


def screen(table: pa.Table, uni: universe.Universe, day: date) -> AnalystDay | list[Any]:
    """A skipped day, or (as a list) tested, cleared and the candidates."""
    if is_holiday(day):
        return AnalystDay(day, 0, 0, [], skipped_reason="federal holiday: no analysis")
    if table.num_rows == 0:
        return AnalystDay(day, 0, 0, [], skipped_reason="no counts in the store")
    panel = detect.Panel.from_table(table, uni)
    ev = detect.evaluate(panel, day)
    if ev is None:
        return AnalystDay(day, 0, 0, [], skipped_reason="no 8-week baseline for this day")
    found = detect.screen(panel, day)
    return [len(ev.series), len(found), found]


def _outage(env: Env, con: db.Connection, exc: Exception, now: datetime) -> Status:
    """Record why the load stopped: some pages committed (incomplete) or none (unavailable)."""
    status: Status = (
        "incomplete_load" if db.get_meta(con, "load_in_progress") else "source_unavailable"
    )
    if route([], exc) != "issue":  # an outage goes to an issue and never to the model
        raise AssertionError("an outage must route to an issue")
    reason = f"The load did not finish ({type(exc).__name__}): {exc}"
    env.sink.open_issue(reason, [], day=now.date())
    return status


def _site(env: Env, now: datetime, pending: RunRecord | None = None) -> None:
    sitedata.build(env.repo, env.site_dir or env.repo / "web" / "src" / "data", now, pending)


def run(env: Env) -> RunRecord:
    """Run once. The envelope check comes first: a configuration that could spend more than the
    ceiling is refused before anything loads or is paid for. A refusal, like any failure, is
    recorded as an error run and re-raised."""
    ai = env.client if env.ai_enabled else None
    spend = Spend()
    started = env.clock()
    progress = Progress(started, step="envelope")
    try:
        envelope = run_envelope(env.config)  # first: a refused run loads and spends nothing
        if ai is not None and env.tally is not None:
            progress.step = "tally"
            env.tally.admit(env.run_id, envelope)
        record = _run(env, ai, spend, progress)
    except Exception as exc:
        message = describe(exc, progress.step)
        log.error("daily run failed", extra={"error": message})
        failure = _record(env, ai, spend, progress, "error", error=message)
        if not progress.recorded and _append_error(env.data, failure, message):
            try:
                _site(env, started.replace(tzinfo=None))
            except Exception as site_exc:  # the first failure is the one raised
                log.error("site build after a failure failed", extra={"error": str(site_exc)})
        raise
    if ai is not None and env.tally is not None:
        settle_if_complete(env.tally, env.run_id, spend.total, True)
    return record


def _append_error(data: Path, failure: RunRecord, message: str) -> bool:
    """Write the error record. If that fails too, say so in one log line with the original error,
    so the second failure never hides the first."""
    try:
        runrecord.append(data / "runs.jsonl", failure)
    except runrecord.AlreadyRecorded:
        log.warning("this attempt already has its record", extra={"error": message})
    except Exception as write_exc:
        log.error(
            "the error record could not be written",
            extra={"error": message, "record_error": describe(write_exc, "record write")},
        )
        return False
    return True


def record_setup_failure(
    repo: Path, run_id: str, attempt: int, ai_enabled: bool, exc: BaseException
) -> None:
    """A failure before `run()` could start (settings, clients, a missing key) is a run too."""
    now = datetime.now(NEW_YORK).isoformat(timespec="seconds")
    message = describe(exc, "setup")
    log.error("daily run could not start", extra={"error": message})
    failure = RunRecord(
        run_id, now, now, "error", 0, 0, ai_enabled=ai_enabled, attempt=attempt, error=message
    )
    _append_error(repo / "data", failure, message)


def _record(
    env: Env, ai: LLMClient | None, spend: Spend, p: Progress, status: Status, **over: Any
) -> RunRecord:
    fields: dict[str, Any] = {
        "rows_loaded": p.rows,
        "pages": p.pages,
        "checks": p.checks,
        "repair": p.repair,
        "analyst_day": p.analyst_day,
        "generation": p.generation,
        "notes": list(p.notes),
        **over,
    }
    return RunRecord(
        run_id=env.run_id,
        started=p.started.isoformat(timespec="seconds"),
        finished=datetime.now(NEW_YORK).isoformat(timespec="seconds"),
        status=status,
        ai_enabled=ai is not None,
        attempt=env.attempt,
        ai_cost=str(spend.total),
        **fields,
    )


def _analyse(
    env: Env, con: db.Connection, ai: LLMClient | None, table: pa.Table, uni: universe.Universe,
    analysed: date, spend: Spend, p: Progress,
) -> tuple[AnalystDay, bool]:  # fmt: skip
    """The day's analysis, and whether the store connection was closed for the investigator."""
    screened = screen(table, uni, analysed)
    if isinstance(screened, AnalystDay):
        return screened, False
    tested, cleared, found = screened
    if not found:
        return AnalystDay(analysed, tested, cleared, []), False
    if ai is None:
        note = (
            f"A source failed earlier in this run ({p.source_failure}): the candidates were "
            "counted, not investigated"
            if env.ai_enabled
            else "AI is not enabled: the candidates were counted, not investigated"
        )
        return AnalystDay(analysed, tested, cleared, [], skipped_reason=note), False
    p.step = "investigation"
    con.close()  # the investigator opens the store itself
    watch = {"on_source_failure": p.fail_source, "stop": lambda: p.source_failure is not None}
    return dailyai.investigate_day(
        env.state / "raw.duckdb", found, ai, analysed, tested, cleared, spend,
        (DEFAULT_STEPS[1], DEFAULT_STEPS[2]), env.investigate_timeout_s,
        **{**env.investigator, **watch},
    ), True  # fmt: skip


def _run(env: Env, ai: LLMClient | None, spend: Spend, p: Progress) -> RunRecord:
    now = p.started.replace(tzinfo=None)
    day = now.date()
    p.step = "spec"
    spec = PipelineSpec.load(env.repo / render.SPEC_PATH)

    def live() -> LLMClient | None:
        """The model, unless a source failed earlier in this run."""
        return None if p.source_failure else ai

    def finish(status: Status) -> RunRecord:
        p.step = "record and site"
        record = _record(env, ai, spend, p, status)
        path = env.data / "runs.jsonl"
        already = runrecord.recorded(path, env.run_id, env.attempt)  # an earlier call's record
        _site(env, now, pending=None if already else record)
        if not already:
            runrecord.append(path, record)
        p.recorded = True
        return record

    p.step = "open store"
    con = db.open(env.state / "raw.duckdb")
    closed = False
    try:
        p.step = "load"
        try:
            result = load.incremental(con, env.socrata, spec, now, env.page_size)
        except (SourceUnavailable, SourceRejected) as exc:
            if isinstance(exc, SourceRejected) and exc.names_missing_column:
                p.step = "schema diagnosis"
                if dailyrepair.schema_change(env, spec, live(), now, spend, p):
                    return finish("checks_failed")  # the load did not finish: no analysis
            p.step = "outage report"
            return finish(_outage(env, con, exc, now))
        p.rows, p.pages, p.generation = result.rows, result.pages, result.generation
        p.checks = _records(result.checks)
        if day.weekday() == SUNDAY:
            p.step = "weekly reconcile"
            try:
                reconcile.weekly(con, env.socrata, spec, now)
            except (SourceUnavailable, SourceRejected) as exc:
                p.fail_source("weekly reconcile", exc)
                log.warning("weekly reconcile skipped", extra={"error": str(exc)})
        failing = failed(result.checks)
        drill_ai = live()
        if route(result.checks, None) == "agent":
            p.step = "repair"
            p.repair = dailyrepair.real_failure(env, spec, live(), failing, now, spend, p)
        elif day.weekday() == SUNDAY and drill_ai and not dailyrepair.real_repair_today(env, day):
            p.step = "drill"
            p.repair = dailyai.drill(
                env.sink, drill_ai, day, spend, DEFAULT_STEPS[0], env.repair_timeout_s
            )
        p.step = "counts"
        uni = universe.load(env.data / "universe.json")
        analysed = day - timedelta(days=load.ANALYSIS_LAG_DAYS)
        table = refresh_counts(con, env.data, result.generation, uni)
        write_vintage(env.data, env.run_id, con, day)
        p.step = "analysis"
        p.analyst_day = analysed.isoformat()
        path = env.data / "analyst" / f"{analysed.isoformat()}.json"
        with day_counts(env.data, table, analysed):  # the pair is written only if both succeed
            if path.exists():
                p.notes.append(f"already analysed: {analysed.isoformat()} is not analysed again")
            else:
                analyst, closed = _analyse(env, con, live(), table, uni, analysed, spend, p)
                p.step = "analyst file"
                write_json(path, to_json(analyst))
        return finish("checks_failed" if failing else "success")
    finally:
        if not closed:
            con.close()
