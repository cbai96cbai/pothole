"""The daily run's answer to failed checks: the repair agent when the model may be called, else an
issue. Covers both ways a failure shows up: the checks on a finished load, and a source that
rejects the load because a field it names is gone (a schema change, diagnosed from a raw sample).
"""

from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from pothole import dailyai, runrecord
from pothole.budget.envelope import DEFAULT_STEPS
from pothole.dailyerror import Progress
from pothole.llm.client import LLMClient
from pothole.repair.decide import rule_outcome
from pothole.repair.validate import failed
from pothole.runrecord import CheckResult, RepairRecord
from pothole.socrata import meta
from pothole.socrata.client import SourceRejected, SourceUnavailable
from pothole.socrata.recent import fetch_batch, fetch_schema_sample
from pothole.spec import checks as spec_checks
from pothole.spec import engine
from pothole.spec.model import PipelineSpec
from pothole.store.load import ANALYSIS_LAG_DAYS

if TYPE_CHECKING:
    from pothole.daily import Env

NOT_ENABLED = "Checks failed and AI is not enabled for this run; a person needs to look."
Sample = tuple[list[dict[str, str]], dict[str, str]]


def _strings(raw: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [{k: str(v) for k, v in r.items() if not k.startswith(":")} for r in raw]


def _fields(env: "Env", spec: PipelineSpec) -> dict[str, str]:
    return {f.name: f.type for f in meta.fields(env.socrata, spec.source.dataset)}


def _failing_sample(env: "Env", spec: PipelineSpec, now: datetime) -> Sample:
    """Yesterday's rows as the source sends them, and the source's fields."""
    rows = _strings(fetch_batch(env.socrata, now.date() - timedelta(days=1)))
    return rows, _fields(env, spec)


def _reason(env: "Env", p: Progress) -> str:
    if env.ai_enabled and p.source_failure:
        return (
            f"Checks failed and a source failed earlier in this run ({p.source_failure}), "
            "so the model was not called; a person needs to look."
        )
    return NOT_ENABLED


def _issue(
    env: "Env", reason: str, failing: list[spec_checks.CheckResult], day: date
) -> RepairRecord:
    filed = env.sink.open_issue(reason, failing, day=day)
    return RepairRecord("issue", False, filed.ref, None, False, "code")


def _file(
    env: "Env", spec: PipelineSpec, ai: LLMClient | None, failing: list[spec_checks.CheckResult],
    sample: Sample, now: datetime, spend: dailyai.Spend, p: Progress,
) -> RepairRecord:  # fmt: skip
    if ai is None:
        return _issue(env, _reason(env, p), failing, now.date())
    rows, fields = sample
    return dailyai.real_repair(
        env.sink, ai, spec, rows, failing, fields, now, spend, DEFAULT_STEPS[0],
        env.repair_timeout_s,
    )  # fmt: skip


def real_failure(
    env: "Env", spec: PipelineSpec, ai: LLMClient | None, failing: list[spec_checks.CheckResult],
    now: datetime, spend: dailyai.Spend, p: Progress,
) -> RepairRecord:  # fmt: skip
    """Checks failed on the window: a volume-only failure is settled by the rule here, on the
    window's own history and before any sample is fetched or the model is considered. Any other
    failure looks at yesterday's rows. A sample that cannot be fetched files an issue and bars
    every later model step of the run."""
    day = now.date()
    decided = rule_outcome(failing)
    if decided is not None:
        return dailyai.file_rule(env.sink, decided, spec, failing, day)
    if ai is None:
        return _issue(env, _reason(env, p), failing, day)
    try:
        sample = _failing_sample(env, spec, now)
    except (SourceUnavailable, SourceRejected) as exc:
        p.fail_source("repair sample", exc)
        return _issue(env, f"The failing rows could not be fetched: {exc}", failing, day)
    again = failed(engine.run(spec, sample[0], [], now).checks)
    if not again:
        reason = (
            "The checks failed on the window but not on yesterday's rows; a person should look."
        )
        return _issue(env, reason, failing, day)
    return _file(env, spec, ai, again, sample, now, spend, p)


def schema_change(
    env: "Env", spec: PipelineSpec, ai: LLMClient | None, now: datetime, spend: dailyai.Spend,
    p: Progress,
) -> bool:  # fmt: skip
    """The source rejected a query naming a field it no longer has (only fields the query must
    name, such as the day field, can do this: the load itself takes every field). That is a
    schema change, not an outage: take a raw sample of the latest complete day, run the engine's
    checks on it and send the failures to repair like any failed check, or file an issue when
    the sample passes. Returns False only when there is no sample to look at; the caller then
    reports an outage."""
    try:
        sample_day = now.date() - timedelta(days=ANALYSIS_LAG_DAYS)
        rows = _strings(fetch_schema_sample(env.socrata, sample_day))
        fields = _fields(env, spec)
    except (SourceUnavailable, SourceRejected) as exc:
        p.fail_source("schema sample", exc)
        return False
    if not rows:
        return False
    results = engine.run(spec, rows, [], now).checks
    failing = failed(results)
    p.checks = [CheckResult(r.name, r.level, r.detail) for r in results]
    if failing:
        p.repair = _file(env, spec, ai, failing, (rows, fields), now, spend, p)
    else:
        reason = (
            "The source rejected the load naming a field it no longer has, but a raw sample "
            "passes every check; a person should look."
        )
        p.repair = _issue(env, reason, [], now.date())
    return True


def real_repair_today(env: "Env", day: date) -> bool:
    today = day.isoformat()
    return any(
        r.repair is not None and not r.repair.drill and r.finished[:10] == today
        for r in runrecord.read(env.data / "runs.jsonl")
    )
