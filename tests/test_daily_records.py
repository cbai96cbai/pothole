"""One terminal record per (run id, attempt), and a day's analysis written once."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from daily_kit import TUESDAY, env_for, forbid_model, run_with_fake_repair, window_rows

from pothole import daily, runrecord
from pothole.repair.outcome import RepairOutcome

COST = Decimal("0.01")


def paying_issue(*a: Any, **k: Any) -> RepairOutcome:
    return RepairOutcome("issue", reason="cannot tell", cost=COST)


def test_a_site_build_failure_leaves_one_error_record_with_the_spend_counted_once(
    repo: Path, isolated_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_with_fake_repair(monkeypatch, paying_issue)
    real_build = daily.sitedata.build
    builds: list[Any] = []

    def build(*args: Any, **kwargs: Any) -> None:
        builds.append(kwargs.get("pending") or (args[3] if len(args) > 3 else None))
        if len(builds) == 1:
            raise RuntimeError("the site build broke")
        real_build(*args, **kwargs)

    monkeypatch.setattr(daily.sitedata, "build", build)
    env, _, _ = env_for(repo, isolated_state, window_rows(TUESDAY, status=""), ai=True)
    with pytest.raises(RuntimeError, match="the site build broke"):
        daily.run(env)
    [only] = runrecord.read(repo / "data" / "runs.jsonl")
    assert only.status == "error" and only.run_id == env.run_id and only.attempt == env.attempt
    assert only.error is not None and only.error.startswith("record and site: RuntimeError: ")
    assert only.ai_cost == str(COST)
    assert builds[0] is not None and builds[0].status == "checks_failed"  # the success was pending
    board = json.loads((repo / "site" / "board.json").read_text())
    assert board["month_ai_cost"] == str(COST) and board["dataset_status"] == "error"


def test_the_site_shows_the_record_before_it_is_appended_and_it_is_appended_once(
    repo: Path, isolated_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env, _, _ = env_for(repo, isolated_state, window_rows(TUESDAY))
    on_disk_during_build: list[int] = []
    real_build = daily.sitedata.build

    def build(*args: Any, **kwargs: Any) -> None:
        on_disk_during_build.append(len(runrecord.read(repo / "data" / "runs.jsonl")))
        real_build(*args, **kwargs)

    monkeypatch.setattr(daily.sitedata, "build", build)
    record = daily.run(env)
    assert on_disk_during_build == [0]  # nothing appended until every step had worked
    assert runrecord.read(repo / "data" / "runs.jsonl") == [record]
    board = json.loads((repo / "site" / "board.json").read_text())
    assert board["last_run"]["run_id"] == record.run_id and board["last_success"] is not None


def test_each_attempt_of_a_run_keeps_its_own_single_record(
    repo: Path, isolated_state: Path
) -> None:
    env, _, _ = env_for(repo, isolated_state, window_rows(TUESDAY))
    daily.run(env)
    env.attempt = 2
    daily.run(env)
    records = runrecord.read(repo / "data" / "runs.jsonl")
    assert [(r.run_id, r.attempt) for r in records] == [(env.run_id, 1), (env.run_id, 2)]


def analysis_path(repo: Path) -> Path:
    return repo / "data" / "analyst" / "2026-11-08.json"


def test_a_rerun_does_not_overwrite_the_days_analysis_and_says_so(
    repo: Path, isolated_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = window_rows(TUESDAY, spike=True)
    env, _, fake = env_for(repo, isolated_state, rows)
    first = daily.run(env)
    written = analysis_path(repo).read_bytes()
    assert first.notes == [] and "not enabled" in json.loads(written)["skipped_reason"]
    fake.rows.extend(  # a late arrival: a new count would now change the finding
        r | {"unique_key": f"late-{i}", ":id": f"late-{i}"}
        for i, r in enumerate(rows[:5])
        if r["created_date"].startswith("2026-11-08")
    )
    calls = forbid_model(monkeypatch)
    again, _, _ = env_for(repo, isolated_state, rows, ai=True)
    again.socrata = fake
    second = daily.run(again)
    assert analysis_path(repo).read_bytes() == written
    assert calls == [] and second.ai_cost == "0"  # not analysed again, so no model call either
    assert second.analyst_day == "2026-11-08"
    assert second.notes == ["already analysed: 2026-11-08 is not analysed again"]
    assert runrecord.read(repo / "data" / "runs.jsonl")[-1].notes == second.notes


def test_a_day_whose_first_run_failed_before_its_analysis_is_analysed_by_the_rerun(
    repo: Path, isolated_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env, _, _ = env_for(repo, isolated_state, window_rows(TUESDAY))
    with monkeypatch.context() as m:
        m.setattr(daily, "screen", lambda *a: (_ for _ in ()).throw(RuntimeError("broke")))
        with pytest.raises(RuntimeError):
            daily.run(env)
    assert not analysis_path(repo).exists()
    second = daily.run(env)
    assert analysis_path(repo).exists() and second.notes == []
    assert date.fromisoformat(second.analyst_day or "") == date(2026, 11, 8)
