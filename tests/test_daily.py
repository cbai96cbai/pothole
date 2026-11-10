"""The daily run, with fakes for the source, the sink and the model. Nothing touches a network."""

import json
import threading
from collections.abc import Callable
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
import pytest
from store.store_fake import FakeSocrata, make_row, outage_on
from webdata.site_repo import scratch_repo

from pothole import daily, dailyai, runrecord
from pothole.analyst.finding import AnalystDay
from pothole.budget.envelope import EnvelopeExceeded, RunConfig, Step
from pothole.drills import baseline, catalog, scoring
from pothole.repair.outcome import RepairOutcome
from pothole.repair.sink import Filed
from pothole.store import counts, db

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


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(daily.meta, "fields", fields_of)
    return scratch_repo(tmp_path / "repo")


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


def test_daily_routes_outage_without_ai(
    repo: Path, isolated_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = forbid_model(monkeypatch)
    env, sink, fake = env_for(repo, isolated_state, window_rows(TUESDAY), ai=True)
    fake.before_query = outage_on(0)
    record = daily.run(env)
    assert record.status == "source_unavailable"
    assert calls == [] and len(sink.issues) == 1 and sink.prs == []
    assert not (repo / "data" / "analyst").exists()
    assert [r.status for r in runrecord.read(repo / "data" / "runs.jsonl")] == [
        "source_unavailable"
    ]
    assert (repo / "site" / "board.json").exists()  # the site still shows the failed run


def test_incomplete_load_makes_no_analysis(
    repo: Path, isolated_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = forbid_model(monkeypatch)
    env, sink, fake = env_for(repo, isolated_state, window_rows(TUESDAY), ai=True, page_size=50)
    fake.before_query = outage_on(2)  # the max query, page 1, then page 2 fails
    record = daily.run(env)
    assert record.status == "incomplete_load" and record.analyst_day is None
    assert calls == [] and len(sink.issues) == 1
    assert not (repo / "data" / "analyst").exists() and not (repo / "data" / "counts").exists()
    assert record.ai_cost == "0"


def test_daily_refuses_over_envelope(repo: Path, isolated_state: Path) -> None:
    env, sink, fake = env_for(
        repo, isolated_state, window_rows(TUESDAY), config=RunConfig(steps=(Step("big", 17),))
    )
    with pytest.raises(EnvelopeExceeded):
        daily.run(env)
    assert fake.queries == [] and not (isolated_state / "raw.duckdb").exists()
    assert not (repo / "data" / "runs.jsonl").exists()


def test_generation_mismatch_regenerates_counts(repo: Path, isolated_state: Path) -> None:
    from pothole.analyst import universe

    env, _, _ = env_for(repo, isolated_state, window_rows(TUESDAY))
    first = daily.run(env)
    data = repo / "data"
    day_file = daily.counts_path(data, date(2026, 11, 8))
    assert json.loads((data / "state.json").read_text())["generation"] == first.generation
    day_file.unlink()
    (data / "state.json").write_text(json.dumps({"generation": "an-older-store"}))
    second = daily.run(env)
    assert json.loads((data / "state.json").read_text())["generation"] == second.generation
    assert second.generation != first.generation
    con = db.open(isolated_state / "raw.duckdb", read_only=True)
    want = counts.regenerate(con, universe.load(data / "universe.json"))
    con.close()
    assert daily.read_counts(day_file).equals(want)


def test_a_days_counts_file_is_never_rewritten(repo: Path, isolated_state: Path) -> None:
    import gzip

    env, _, _ = env_for(repo, isolated_state, window_rows(TUESDAY))
    daily.run(env)
    path = daily.counts_path(repo / "data", date(2026, 11, 8))
    before = path.read_bytes()
    assert before[4:8] == b"\0\0\0\0"  # gzip mtime 0: the same table always gives the same bytes
    lines = gzip.decompress(before).decode().splitlines()
    assert lines[0] == '"date","borough","board","complaint_type","n"'
    keys = [ln.split(",")[:3] for ln in lines[1:]]
    assert keys == sorted(keys)
    path.write_bytes(before + b"")  # unchanged
    marker = path.stat().st_mtime_ns
    (repo / "data" / "state.json").write_text(json.dumps({"generation": "other"}))
    daily.run(env)  # a later load of the same day: the committed file stays
    assert path.read_bytes() == before and path.stat().st_mtime_ns == marker
    assert sorted(p.name for p in (repo / "data" / "counts").iterdir()) == ["2026-11-08.csv.gz"]


def test_a_matching_generation_reads_the_days_file(repo: Path, isolated_state: Path) -> None:
    from pothole.analyst import universe

    uni = universe.load(repo / "data" / "universe.json")
    env, _, _ = env_for(repo, isolated_state, window_rows(TUESDAY))
    record = daily.run(env)
    path = daily.counts_path(repo / "data", date(2026, 11, 8))
    marker = daily.read_counts(path).slice(0, 3)
    daily.write_counts(path, marker)
    con = db.open(isolated_state / "raw.duckdb")
    assert record.generation is not None
    kept = daily.refresh_counts(con, repo / "data", record.generation, uni, date(2026, 11, 8))
    con.close()
    assert kept.equals(marker)


def run_with_fake_repair(
    monkeypatch: pytest.MonkeyPatch, fn: Callable[..., RepairOutcome]
) -> list[int]:
    calls: list[int] = []

    def fake(*a: Any, **k: Any) -> RepairOutcome:
        calls.append(1)
        return fn(*a, **k)

    monkeypatch.setattr(dailyai.agent, "repair", fake)
    return calls


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
    assert len(sink.issues) == 1


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


def baseline_repairer(spec, rows, failing, fields, client, **k):  # type: ignore[no-untyped-def]
    return baseline.repair(spec, failing, rows, fields)


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


def test_a_holiday_is_skipped_with_its_reason(repo: Path, isolated_state: Path) -> None:
    holiday = datetime(2026, 11, 13, 12, 0)  # the analysis day is 2026-11-11
    env, _, _ = env_for(repo, isolated_state, window_rows(holiday), holiday)
    daily.run(env)
    day = json.loads((repo / "data" / "analyst" / "2026-11-11.json").read_text())
    assert day["findings"] == [] and "holiday" in day["skipped_reason"]


def test_a_complete_run_writes_the_record_counts_vintages_and_site(
    repo: Path, isolated_state: Path
) -> None:
    env, sink, _ = env_for(repo, isolated_state, window_rows(TUESDAY))
    record = daily.run(env)
    assert record.status == "success" and record.analyst_day == "2026-11-08"
    assert record.rows_loaded == 200 and record.checks and sink.issues == []
    [vintage] = (repo / "data" / "vintages.jsonl").read_text().splitlines()
    assert json.loads(vintage)["run_id"] == record.run_id
    assert runrecord.read(repo / "data" / "runs.jsonl") == [record]
    assert (repo / "site" / "findings" / "index.json").exists()
    assert (repo / "data" / "analyst" / "2026-11-08.json").exists()
