"""A source failure in a run bars every later model step of that run: the weekly reconcile, the
repair sample, and (while the investigator works) the weather and events lookups."""

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from daily_kit import SUNDAY, TUESDAY, env_for, forbid_model, window_rows

from pothole import daily, dailyai
from pothole.analyst.finding import AnalystDay
from pothole.socrata.client import SourceUnavailable


def fail_when(select_starts: str = "", has: str = "") -> Any:
    def hook(n: int, params: Mapping[str, str]) -> None:
        if (select_starts and params.get("$select", "").startswith(select_starts)) or (
            has and has in params
        ):
            raise SourceUnavailable("fake outage")

    return hook


def test_a_sunday_without_a_source_failure_would_drill_and_investigate(
    repo: Path, isolated_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = forbid_model(monkeypatch)  # control: both model steps are reached
    env, _, _ = env_for(repo, isolated_state, window_rows(SUNDAY, spike=True), SUNDAY, ai=True)
    daily.run(env)
    assert len(calls) == 2


def test_a_reconcile_outage_bars_the_drill_and_the_investigation(
    repo: Path, isolated_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = forbid_model(monkeypatch)
    env, sink, fake = env_for(
        repo, isolated_state, window_rows(SUNDAY, spike=True), SUNDAY, ai=True
    )
    fake.before_query = fail_when(has="$group")  # the reconcile's per-day counts
    record = daily.run(env)
    assert calls == [] and record.repair is None and record.ai_cost == "0"
    day = json.loads((repo / "data" / "analyst" / "2026-11-13.json").read_text())
    assert day["findings"] == [] and "weekly reconcile: SourceUnavailable" in day["skipped_reason"]
    assert record.status == "success"  # the load itself finished


def test_a_reconcile_outage_sends_a_failed_check_to_an_issue_not_the_agent(
    repo: Path, isolated_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = forbid_model(monkeypatch)
    rows = window_rows(SUNDAY, status="")
    env, sink, fake = env_for(repo, isolated_state, rows, SUNDAY, ai=True)
    fake.before_query = fail_when(has="$group")
    record = daily.run(env)
    assert calls == [] and record.repair is not None and record.repair.action == "issue"
    [issue] = sink.issues
    assert "source failed earlier" in issue["reason"] and "weekly reconcile" in issue["reason"]


def test_a_repair_sample_failure_bars_the_investigation(
    repo: Path, isolated_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = forbid_model(monkeypatch)
    env, sink, fake = env_for(
        repo, isolated_state, window_rows(TUESDAY, spike=True, status=""), ai=True
    )
    fake.before_query = fail_when(select_starts="*")  # yesterday's raw rows
    record = daily.run(env)
    assert calls == [] and record.ai_cost == "0"
    assert record.repair is not None and record.repair.action == "issue"
    assert "could not be fetched" in sink.issues[0]["reason"]
    day = json.loads((repo / "data" / "analyst" / "2026-11-08.json").read_text())
    assert day["findings"] == [] and "repair sample: SourceUnavailable" in day["skipped_reason"]


@pytest.mark.parametrize("label", ["weather", "events"])
def test_a_weather_or_events_failure_during_the_investigation_bars_what_follows(
    repo: Path, isolated_state: Path, monkeypatch: pytest.MonkeyPatch, label: str
) -> None:
    seen: list[bool] = []

    def fake_day(store: Path, found: list[Any], client: Any, **k: Any) -> AnalystDay:
        seen.append(k["stop"]())  # no failure yet
        k["on_source_failure"](label, TimeoutError("slow"))
        seen.append(k["stop"]())  # the candidates still to come would not start
        return AnalystDay(k["analysed"], k["tested"], k["cleared"], [])

    monkeypatch.setattr(dailyai.investigate, "day", fake_day)
    env, _, _ = env_for(repo, isolated_state, window_rows(TUESDAY, spike=True), ai=True)
    daily.run(env)
    assert seen == [False, True]
