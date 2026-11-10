"""One daily run, as the runner records it and the site reads it: the contract shared by the
daily runner and `sitedata`. Records are appended to `data/runs.jsonl`, one JSON object per line."""

import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from pothole.analyst.finding import to_json

Status = Literal["success", "checks_failed", "source_unavailable", "incomplete_load", "error"]
RepairAction = Literal["patch", "issue", "no_change", "none"]
STATUSES: tuple[str, ...] = (
    "success",
    "checks_failed",
    "source_unavailable",
    "incomplete_load",
    "error",
)
ACTIONS: tuple[str, ...] = ("patch", "issue", "no_change", "none")


Level = Literal["pass", "warn", "fail"]  # the engine's own words
LEVELS: tuple[str, ...] = ("pass", "warn", "fail")


@dataclass(frozen=True)
class CheckResult:
    name: str
    level: Level
    detail: str

    def __post_init__(self) -> None:
        if self.level not in LEVELS:
            raise ValueError(f"unknown check level: {self.level!r}")


@dataclass(frozen=True)
class RepairRecord:
    """What the repair step did in a run. `drill` marks a planted fault (a practice run, never a
    real failure); `not_judged` is true for a proposal on a real failure that nothing has judged."""

    action: RepairAction
    drill: bool
    url: str | None
    verdict: str | None
    not_judged: bool


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    started: str
    finished: str
    status: Status
    rows_loaded: int
    pages: int
    checks: list[CheckResult] = field(default_factory=list)
    repair: RepairRecord | None = None
    analyst_day: str | None = None
    ai_cost: str = "0"
    generation: str | None = None
    ai_enabled: bool = False  # the run was allowed to call the model
    attempt: int = 1  # GitHub re-runs keep the run id and raise the attempt
    error: str | None = None  # an error run: "step: Class: message"
    notes: list[str] = field(default_factory=list)  # what the run left undone, in plain words

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"unknown run status: {self.status!r}")
        for stamp in (self.started, self.finished):
            datetime.fromisoformat(stamp)
        if self.analyst_day is not None:
            datetime.fromisoformat(self.analyst_day)
        Decimal(self.ai_cost)
        if self.repair is not None and self.repair.action not in ACTIONS:
            raise ValueError(f"unknown repair action: {self.repair.action!r}")


def append(path: Path, record: RunRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(to_json(record), sort_keys=True) + "\n")


def _record(raw: dict[str, Any]) -> RunRecord:
    repair = raw.get("repair")
    return RunRecord(
        **{
            **raw,
            "checks": [CheckResult(**c) for c in raw.get("checks", [])],
            "repair": RepairRecord(**repair) if repair is not None else None,
        }
    )


def read(path: Path) -> list[RunRecord]:
    """All records in file order; a missing file is an empty history, a bad line is an error."""
    if not path.exists():
        return []
    out: list[RunRecord] = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            out.append(_record(json.loads(line)))
        except (ValueError, TypeError) as e:
            raise ValueError(f"{path}:{n}: bad run record: {e}") from e
    return out
