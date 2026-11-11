"""The daily run's model steps, with a fake repairer: repair, drill, timeouts, the investigator."""

import json
import threading
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
import pytest
from daily_kit import (  # noqa: F401
    SUNDAY,
    TUESDAY,
    NoModel,
    Sink,
    baseline_repairer,
    env_for,
    forbid_model,
    run_with_fake_repair,
    window_rows,
)  # fmt: skip

from pothole import daily, dailyai, runrecord
from pothole.analyst.finding import AnalystDay
from pothole.budget.envelope import DEFAULT_STEPS
from pothole.drills import baseline, catalog, scoring
from pothole.repair.outcome import RepairOutcome


def test_a_failed_check_goes_to_the_agent_and_its_issue_is_filed_unjudged(
    repo: Path, isolated_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = run_with_fake_repair(
        monkeypatch,
        lambda *a, **k: RepairOutcome("issue", reason="cannot tell", cost=Decimal("0.01")),
    )
    env, sink, _ = env_for(repo, isolated_state, window_rows(TUESDAY, status=""), ai=True)
    record = daily.run(env)
    assert record.status == "checks_failed" and calls == [1]
    assert record.repair is not None and record.repair.action == "issue"
    assert not record.repair.drill and record.repair.verdict is None
    assert record.ai_cost == "0.01" and len(sink.issues) == 1


def test_a_failed_check_with_ai_off_files_an_issue_and_never_calls_the_model(
    repo: Path, isolated_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = forbid_model(monkeypatch)
    env, sink, _ = env_for(repo, isolated_state, window_rows(TUESDAY, status=""), ai=False)
    record = daily.run(env)
    assert calls == [] and record.ai_enabled is False and record.ai_cost == "0"
    assert record.repair is not None and record.repair.action == "issue"
    assert record.repair.decided_by == "code" and len(sink.issues) == 1


def test_ai_off_never_calls_the_model_even_with_candidates_and_on_a_sunday(
    repo: Path, isolated_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = forbid_model(monkeypatch)
    env, sink, _ = env_for(repo, isolated_state, window_rows(SUNDAY, spike=True), SUNDAY, ai=False)
    record = daily.run(env)
    assert calls == [] and record.repair is None and sink.prs == [] and sink.issues == []
    day = json.loads((repo / "data" / "analyst" / "2026-11-13.json").read_text())
    assert day["cleared"] == 1 and day["findings"] == []
    assert "not enabled" in day["skipped_reason"]


def test_drill_only_on_sunday_without_real_repair(
    repo: Path, isolated_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = catalog.load_spec()
    solvable = next(  # a dev case the non-AI baseline repairs correctly
        c
        for c in catalog.load()
        if c.split == "dev"
        and c.expected_action == "patch"
        and scoring.run_case(c, spec, baseline.repair).correct
    )
    monkeypatch.setattr(dailyai, "pick_case", lambda day: solvable)
    calls = run_with_fake_repair(monkeypatch, baseline_repairer)
    # a Tuesday: no drill
    env, sink, _ = env_for(repo, isolated_state, window_rows(TUESDAY), ai=True)
    assert daily.run(env).repair is None and calls == []
    # a Sunday with no real repair today: one drill, filed with the judge's verdict
    env, sink, _ = env_for(repo, isolated_state, window_rows(SUNDAY), SUNDAY, ai=True)
    record = daily.run(env)
    assert calls == [1] and len(sink.prs) == 1, sink.issues
    pr = sink.prs[0]
    assert pr["drill"] is True and pr["verdict"] and pr["verdict"].startswith("correct")
    assert (
        record.repair is not None and record.repair.drill and record.repair.verdict == pr["verdict"]
    )
    assert record.repair.not_judged is False
    # a Sunday on which a real repair already ran today: no drill
    runrecord.append(
        repo / "data" / "runs.jsonl",
        runrecord.RunRecord(
            "earlier",
            "2026-11-15T06:00:00-05:00",
            "2026-11-15T06:05:00-05:00",
            "checks_failed",
            1,
            1,
            repair=runrecord.RepairRecord("patch", False, "u", None, True),
        ),  # fmt: skip
    )
    env, sink, _ = env_for(repo, isolated_state, window_rows(SUNDAY), SUNDAY, ai=True)
    assert daily.run(env).repair is None and calls == [1] and sink.prs == []


def test_a_drill_cannot_be_filed_without_a_verdict() -> None:
    with pytest.raises(ValueError):
        dailyai._file(
            Sink(),
            RepairOutcome("patch"),
            None,
            [],
            [],
            date(2026, 11, 15),
            True,
            None,  # type: ignore[arg-type]
        )


def test_a_timed_out_repair_is_recorded_and_the_run_goes_on(
    repo: Path, isolated_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = threading.Event()
    run_with_fake_repair(monkeypatch, lambda *a, **k: release.wait(30) and RepairOutcome("issue"))
    env, sink, _ = env_for(
        repo, isolated_state, window_rows(TUESDAY, status=""), ai=True, repair_timeout_s=0.05
    )
    record = daily.run(env)
    release.set()
    assert record.repair is not None and record.repair.action == "issue"
    assert (
        "did not finish" in sink.issues[-1]["reason"] and "StepTimeout" in sink.issues[-1]["reason"]
    )
    assert Decimal(record.ai_cost) == dailyai.slot_max(daily.DEFAULT_STEPS[0])
    assert (repo / "data" / "analyst" / "2026-11-08.json").exists()  # the analyst still ran
    assert (repo / "site" / "board.json").exists()


def test_a_timed_out_investigation_keeps_the_counts_and_says_so(
    repo: Path, isolated_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = threading.Event()
    monkeypatch.setattr(dailyai.investigate, "day", lambda *a, **k: release.wait(30))
    env, _, _ = env_for(
        repo, isolated_state, window_rows(TUESDAY, spike=True), ai=True, investigate_timeout_s=0.05
    )
    record = daily.run(env)
    release.set()
    day = json.loads((repo / "data" / "analyst" / "2026-11-08.json").read_text())
    assert day["cleared"] == 1 and day["findings"] == []
    assert day["skipped_reason"] == dailyai.TIMED_OUT
    assert Decimal(record.ai_cost) == sum(
        (dailyai.slot_max(s) for s in daily.DEFAULT_STEPS[1:]), Decimal(0)
    )
    assert record.status == "success" and (repo / "site" / "board.json").exists()


def test_the_investigator_gets_the_store_path_with_no_other_connection_open(
    repo: Path, isolated_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[Any] = []

    def fake_day(store: Path, found: list[Any], client: Any, **k: Any) -> AnalystDay:
        # a read-only open fails while a read-write connection to the file is still open
        duckdb.connect(str(store), read_only=True).close()
        seen.append((store, [c.complaint_type for c in found]))
        return AnalystDay(k["analysed"], k["tested"], k["cleared"], [])

    monkeypatch.setattr(dailyai.investigate, "day", fake_day)
    env, _, _ = env_for(repo, isolated_state, window_rows(TUESDAY, spike=True), ai=True)
    daily.run(env)
    assert seen == [(isolated_state / "raw.duckdb", ["Illegal Parking"])]


def _volume(count: int) -> Any:
    from pothole.spec.checks import CheckResult

    detail = (
        f"{count} rows against a typical 2000 over 4 earlier same-weekday counts; "
        "allowed 1000 to 4000"
    )
    return CheckResult("volume", "fail", detail, [])


def test_a_volume_only_window_failure_is_decided_before_any_sample_or_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from pothole import dailyrepair
    from pothole.spec.model import PipelineSpec

    def no_sample(*a: Any, **k: Any) -> Any:
        raise AssertionError("a sample was fetched")

    monkeypatch.setattr(dailyrepair, "_failing_sample", no_sample)
    calls = forbid_model(monkeypatch)
    spec = catalog.load_spec()
    assert isinstance(spec, PipelineSpec)
    for count, action, ai in ((300, "issue", NoModel()), (6000, "no_change", None)):
        sink = Sink()
        env = SimpleNamespace(sink=sink, ai_enabled=ai is not None)
        rec = dailyrepair.real_failure(
            env,
            spec,
            ai,
            [_volume(count)],
            datetime(2026, 11, 10, 12),
            None,
            None,  # type: ignore[arg-type]
        )
        assert rec.action == action and rec.decided_by == "rule" and not rec.drill
    assert calls == []


def test_the_drill_decides_a_volume_case_by_rule_and_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = forbid_model(monkeypatch)
    spike = next(c for c in catalog.load() if c.split == "dev" and c.family == "spike")
    monkeypatch.setattr(dailyai, "pick_case", lambda day: spike)
    sink = Sink()
    spend = dailyai.Spend()
    rec = dailyai.drill(sink, NoModel(), date(2026, 11, 15), spend, DEFAULT_STEPS[0])  # type: ignore[arg-type]
    assert rec.decided_by == "rule" and rec.drill and calls == []
    assert rec.verdict is not None and "decided by rule" in rec.verdict


def test_a_step_the_model_did_not_finish_is_filed_as_decided_by_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pothole.spec.checks import CheckResult

    def crash(*a: Any, **k: Any) -> Any:
        raise RuntimeError("model down")

    monkeypatch.setattr(dailyai.agent, "repair", crash)
    spec = catalog.load_spec()
    sink = Sink()
    cast = CheckResult("cast:created_date", "fail", "x", [])
    rec = dailyai.real_repair(
        sink, NoModel(), spec, [], [cast], {}, datetime(2026, 11, 10, 12), dailyai.Spend(),  # type: ignore[arg-type]
        DEFAULT_STEPS[0],
    )  # fmt: skip
    assert rec.action == "issue" and rec.decided_by == "code"


def test_an_old_record_without_decided_by_reads_as_unknown() -> None:
    from pothole.runrecord import RepairRecord

    assert RepairRecord("issue", False, None, None, False).decided_by == "unknown"
