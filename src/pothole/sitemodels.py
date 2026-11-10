"""The shape of every JSON file the site reads. `web/schema/` is generated from these, and
`web/src/types.ts` mirrors them."""

from typing import Any

from pydantic import BaseModel, ConfigDict


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Check(Model):
    name: str
    level: str
    detail: str


class RunSummary(Model):
    run_id: str
    started: str
    finished: str
    status: str
    rows_loaded: int
    pages: int
    analyst_day: str | None
    ai_cost: str
    generation: str | None


class LastCheck(Model):
    name: str
    level: str
    detail: str
    run_id: str
    finished: str


class LatestDay(Model):
    day: str
    tested: int
    cleared: int
    findings: int
    skipped_reason: str | None


class Board(Model):
    dataset_status: str
    last_run: RunSummary | None
    last_success: RunSummary | None
    checks: list[LastCheck]
    latest_day: LatestDay | None
    month: str | None
    month_ai_cost: str


class Repair(Model):
    action: str
    drill: bool
    url: str | None
    verdict: str | None
    not_judged: bool


class IncidentItem(Model):
    run_id: str
    started: str
    status: str
    repair: Repair
    failed_checks: list[Check]
    ai_cost: str


class Incidents(Model):
    incidents: list[IncidentItem]


class Drills(Model):
    drills: list[IncidentItem]


class FindingCard(Model):
    id: str
    status: str
    borough: str
    board: int
    complaint_type: str
    observed: int
    expected: float
    ratio: float
    q: float
    summary: str


class FindingDay(Model):
    day: str
    tested: int
    cleared: int
    skipped_reason: str | None
    findings: list[FindingCard]


class FindingsIndex(Model):
    days: list[FindingDay]


class Rate(Model):
    correct: int
    of: int
    rate: float | None


class Share(Model):
    count: int
    of: int
    rate: float | None


class Detected(Model):
    detected: int
    of: int
    rate: float | None


class Family(Model):
    expected: str
    cases: int
    correct: int
    detected: int


class CaseFailure(Model):
    id: str
    family: str
    expected: str
    detected: bool
    action: str
    correct: bool
    reason: str


class RepairSummary(Model):
    repairer: str
    split: str
    cases: int
    repair_success: Rate | None
    issue_accuracy: Rate | None
    no_change_accuracy: Rate | None
    repairable_answered_with_issue: Share
    crashed: int
    detection_coverage_fault_cases: Detected
    detection_coverage_benign_control: Detected
    per_family: dict[str, Family]
    failures: list[CaseFailure]


class Repairs(Model):
    summaries: list[RepairSummary]
    agent_run: bool


class FlagRow(Model):
    borough: str
    board: int
    complaint_type: str
    observed: int
    expected: float
    ratio: float
    q: float


class BacktestDay(Model):
    day: str
    series_tested: int
    cleared_the_bar: int
    top: list[FlagRow]


class FlagsPerDay(Model):
    min: int
    median: float
    max: int


class Backtest(Model):
    label: str
    window: list[str]
    days_tested: int
    flags_per_day: FlagsPerDay
    days: list[BacktestDay]


class Delay(Model):
    label: str
    reload_at: str
    rows_measured_at_lag_0: int
    rows_measured_note: str
    rows_by_lag_days: dict[str, int]
    share_by_lag_days: dict[str, float]


class NullSimulation(Model):
    label: str
    seed: int
    series: int
    days_tested: int
    days_simulated_after_lead_in: int
    rule: str
    dispersion: str
    share_of_days_with_a_candidate: dict[str, float]


class Runs(Model):
    runs: list[RunSummary]


class Facts(Model):
    """Every figure the pages state in words, taken from the code and files that define it."""

    window_days: int
    analysis_lag_days: int
    complaint_types: int
    districts: int
    max_investigations: int
    calls_per_run: int
    max_cost_per_call: str
    run_envelope: str
    run_ceiling: str
    ceiling_days: int
    monthly_limit: str
    price_input_per_million: str
    price_output_per_million: str


class Meta(Model):
    facts: Facts
    generated: str
    last_success_finished: str | None
    last_success_run_id: str | None


def as_dict(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json")
