"""Shared fakes and fixtures for the daily run's tests."""

from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from store.store_fake import FakeSocrata, make_row

from pothole import daily, dailyai
from pothole.drills import baseline
from pothole.repair.outcome import RepairOutcome
from pothole.repair.sink import Filed

REAL = Path(__file__).parents[1]
TUESDAY = datetime(2026, 11, 10, 12, 0)
SUNDAY = datetime(2026, 11, 15, 12, 0)
KEEP_OUT = AssertionError("the model was called")


class Sink:
    def __init__(self) -> None:
        self.prs: list[dict[str, Any]] = []
        self.issues: list[dict[str, Any]] = []

    def open_pr(self, outcome, spec, before, after, *, day, drill=False, verdict=None):  # type: ignore[no-untyped-def]
        self.prs.append({"drill": drill, "verdict": verdict, "day": day, "outcome": outcome})
        return Filed("pr", f"pr-{len(self.prs)}", "branch")

    def open_issue(self, reason, failing, *, day, drill=False):  # type: ignore[no-untyped-def]
        self.issues.append({"reason": reason, "drill": drill, "failing": failing})
        return Filed("issue", f"issue-{len(self.issues)}")


class NoModel:
    def call(self, *a: Any, **k: Any) -> Any:
        raise KEEP_OUT


def window_rows(now: datetime, spike: bool = False, status: str = "Open") -> list[dict[str, str]]:
    """The analysis day and its 8 same weekdays before, plus yesterday: 20 rows a day, 3 of them
    in series A on baseline days. With `spike`, the analysis day has 12 in A and 8 in B."""
    d = now.date() - timedelta(days=2)
    days = [d - timedelta(days=7 * k) for k in range(9)] + [now.date() - timedelta(days=1)]
    rows: list[dict[str, str]] = []
    n = 0
    for day in days:
        a = 12 if spike and day == d else 3
        for i in range(20):
            kind = "Illegal Parking" if i < a else "Noise - Residential"
            rows.append(
                make_row(
                    n, n * 0.01, created=f"{day}T10:00:00.000", complaint_type=kind, status=status
                )
            )
            n += 1
    return rows


def fields_of(*a: Any, **k: Any) -> list[Any]:
    from pothole.socrata.meta import Field

    return [Field("unique_key", "text")]


def env_for(
    repo: Path,
    state: Path,
    rows: list[dict[str, str]],
    now: datetime = TUESDAY,
    ai: bool = False,
    **over: Any,
) -> tuple[daily.Env, Sink, FakeSocrata]:
    fake, sink = FakeSocrata(rows), Sink()
    env = daily.Env(
        repo=repo,
        state=state,
        socrata=fake,
        sink=sink,
        client=NoModel(),  # type: ignore[arg-type]
        ai_enabled=ai,
        clock=lambda: now.replace(tzinfo=daily.NEW_YORK),
        site_dir=repo / "site",
        **over,
    )
    return env, sink, fake


def forbid_model(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def boom(*a: Any, **k: Any) -> Any:
        calls.append("repair")
        raise KEEP_OUT

    monkeypatch.setattr(dailyai.agent, "repair", boom)
    monkeypatch.setattr(dailyai.investigate, "day", boom)
    return calls


def run_with_fake_repair(
    monkeypatch: pytest.MonkeyPatch, fn: Callable[..., RepairOutcome]
) -> list[int]:
    calls: list[int] = []

    def fake(*a: Any, **k: Any) -> RepairOutcome:
        calls.append(1)
        return fn(*a, **k)

    monkeypatch.setattr(dailyai.agent, "repair", fake)
    return calls


def baseline_repairer(spec, rows, failing, fields, client, **k):  # type: ignore[no-untyped-def]
    return baseline.repair(spec, failing, rows, fields)
