"""One daily run: load, check, repair or drill, analyse, record, publish the site data.

Order, and why: the envelope check comes first, before anything is loaded or paid for. A source
outage or an incomplete load is recorded and filed as an issue; it never calls the model and never
produces new analysis. Checks that fail go to the repair agent (only when AI is enabled), else to an
issue. A Sunday with no failure and no real repair earlier that day runs one judged drill. The
analyst works on the day two days back, only after a complete load. `last_success` is written by
the load itself, after the whole window loaded and the checks ran.
"""

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq

from pothole import dailyai, runrecord, sitedata
from pothole.analyst import detect, universe
from pothole.analyst.finding import AnalystDay, to_json
from pothole.analyst.holidays import is_holiday
from pothole.budget.envelope import DEFAULT_STEPS, RunConfig, run_envelope
from pothole.budget.tally import Tally, settle_if_complete
from pothole.cli import fetch_batch
from pothole.dailyai import Sink, Spend
from pothole.llm.client import LLMClient
from pothole.logs import get_logger
from pothole.repair.routing import route
from pothole.repair.validate import failed
from pothole.runrecord import CheckResult, RepairRecord, RunRecord, Status
from pothole.socrata import meta
from pothole.socrata.client import Socrata, SourceRejected, SourceUnavailable
from pothole.spec import checks as spec_checks
from pothole.spec import engine
from pothole.spec.model import PipelineSpec
from pothole.store import counts, db, load, reconcile

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
    return [CheckResult(r.name, "ok" if r.level == "pass" else r.level, r.detail) for r in results]


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n")


def refresh_counts(
    con: db.Connection, data: Path, generation: str, uni: universe.Universe
) -> pa.Table:
    """The counts table of this store generation. `data/state.json` names the generation the
    counts file was derived from; any other generation, or no file, derives it again."""
    state_path, counts_path = data / "state.json", data / "counts.parquet"
    try:
        held = json.loads(state_path.read_text()).get("generation")
    except (FileNotFoundError, ValueError):
        held = None
    if held == generation and counts_path.exists():
        return pq.read_table(counts_path)
    table = counts.regenerate(con, uni)
    counts_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, counts_path)
    _write_json(state_path, {"generation": generation})
    return table


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


def _failing_sample(
    env: Env, spec: PipelineSpec, now: datetime
) -> tuple[list[dict[str, str]], dict[str, str]]:
    """Yesterday's rows as the source sends them, and the source's fields."""
    raw = fetch_batch(env.socrata, now.date() - timedelta(days=1))
    rows = [{k: str(v) for k, v in r.items() if not k.startswith(":")} for r in raw]
    fields = {f.name: f.type for f in meta.fields(env.socrata, spec.source.dataset)}
    return rows, fields


def _real_failure(
    env: Env, spec: PipelineSpec, ai: LLMClient | None, failing: list[spec_checks.CheckResult],
    now: datetime, spend: Spend,
) -> RepairRecord:  # fmt: skip
    day = now.date()
    if ai is None:
        reason = "Checks failed and AI is not enabled for this run; a person needs to look."
        filed = env.sink.open_issue(reason, failing, day=day)
        return RepairRecord("issue", False, filed.ref, None, False)
    try:
        rows, fields = _failing_sample(env, spec, now)
    except (SourceUnavailable, SourceRejected) as exc:
        filed = env.sink.open_issue(
            f"The failing rows could not be fetched: {exc}", failing, day=day
        )
        return RepairRecord("issue", False, filed.ref, None, False)
    again = failed(engine.run(spec, rows, [], now).checks)
    if not again:
        reason = (
            "The checks failed on the window but not on yesterday's rows; a person should look."
        )
        filed = env.sink.open_issue(reason, failing, day=day)
        return RepairRecord("issue", False, filed.ref, None, False)
    return dailyai.real_repair(
        env.sink, ai, spec, rows, again, fields, now, spend, DEFAULT_STEPS[0], env.repair_timeout_s
    )


def _real_repair_today(env: Env, day: date) -> bool:
    today = day.isoformat()
    return any(
        r.repair is not None and not r.repair.drill and r.finished[:10] == today
        for r in runrecord.read(env.data / "runs.jsonl")
    )


def _finish(env: Env, record: RunRecord, now: datetime) -> RunRecord:
    runrecord.append(env.data / "runs.jsonl", record)
    sitedata.build(env.repo, env.site_dir or env.repo / "web" / "src" / "data", now)
    return record


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


def run(env: Env) -> RunRecord:
    """Run once. Raises EnvelopeExceeded before doing anything when the configuration could spend
    more than the ceiling."""
    envelope = run_envelope(env.config)
    ai = env.client if env.ai_enabled else None
    if ai is not None and env.tally is not None:
        env.tally.admit(env.run_id, envelope)
    spend = Spend()
    record = _run(env, ai, spend)
    if ai is not None and env.tally is not None:
        settle_if_complete(env.tally, env.run_id, spend.total, True)
    return record


def _run(env: Env, ai: LLMClient | None, spend: Spend) -> RunRecord:
    started = env.clock()
    now = started.replace(tzinfo=None)
    day = now.date()
    spec = PipelineSpec.load(env.repo / "pipelines" / "311.yaml")

    def record(status: Status, **fields: Any) -> RunRecord:
        return RunRecord(
            run_id=env.run_id,
            started=started.isoformat(timespec="seconds"),
            finished=datetime.now(NEW_YORK).isoformat(timespec="seconds"),
            status=status,
            ai_enabled=ai is not None,
            ai_cost=str(spend.total),
            **{"rows_loaded": 0, "pages": 0, **fields},
        )

    con = db.open(env.state / "raw.duckdb")
    closed = False
    try:
        try:
            result = load.incremental(con, env.socrata, spec, now, env.page_size)
        except (SourceUnavailable, SourceRejected) as exc:
            return _finish(env, record(_outage(env, con, exc, now)), now)
        if day.weekday() == SUNDAY:
            try:
                reconcile.weekly(con, env.socrata, spec, now)
            except (SourceUnavailable, SourceRejected) as exc:
                log.warning("weekly reconcile skipped", extra={"error": str(exc)})
        failing = failed(result.checks)
        repair: RepairRecord | None = None
        if route(result.checks, None) == "agent":
            repair = _real_failure(env, spec, ai, failing, now, spend)
        elif day.weekday() == SUNDAY and ai is not None and not _real_repair_today(env, day):
            repair = dailyai.drill(env.sink, ai, day, spend, DEFAULT_STEPS[0], env.repair_timeout_s)
        uni = universe.load(env.data / "universe.json")
        table = refresh_counts(con, env.data, result.generation, uni)
        _write_vintage(env, con, day)
        analysed = day - timedelta(days=load.ANALYSIS_LAG_DAYS)
        screened = screen(table, uni, analysed)
        if isinstance(screened, AnalystDay):
            analyst = screened
        else:
            tested, cleared, found = screened
            analyst = AnalystDay(analysed, tested, cleared, [])
            if found and ai is not None:
                con.close()
                closed = True  # the investigator opens the store itself
                analyst = dailyai.investigate_day(
                    env.state / "raw.duckdb", found, ai, analysed, tested, cleared, spend,
                    (DEFAULT_STEPS[1], DEFAULT_STEPS[2]), env.investigate_timeout_s,
                    **env.investigator,
                )  # fmt: skip
            elif found:
                note = "AI is not enabled: the candidates were counted, not investigated"
                analyst = AnalystDay(analysed, tested, cleared, [], skipped_reason=note)
        _write_json(env.data / "analyst" / f"{analysed.isoformat()}.json", to_json(analyst))
        return _finish(
            env,
            record(
                "checks_failed" if failing else "success",
                rows_loaded=result.rows,
                pages=result.pages,
                checks=_records(result.checks),
                repair=repair,
                analyst_day=analysed.isoformat(),
                generation=result.generation,
            ),
            now,
        )
    finally:
        if not closed:
            con.close()


def _write_vintage(env: Env, con: db.Connection, day: date) -> None:
    """One line per run: how many rows each created day held when this run finished loading."""
    line = {"run_id": env.run_id, "run_date": day.isoformat(),
            "held": [[d.isoformat(), n] for d, n in counts.vintage(con, day)]}  # fmt: skip
    path = env.data / "vintages.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, sort_keys=True) + "\n")
