"""The daily run: outage, incomplete load, envelope, counts, the record and the error path."""

import json
from datetime import date, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pytest
from daily_kit import SUNDAY, TUESDAY, env_for, forbid_model, window_rows  # noqa: F401
from store.store_fake import outage_on

from pothole import daily, dailycounts, runrecord
from pothole.budget.envelope import EnvelopeExceeded, RunConfig, Step
from pothole.store import counts, db


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
    [last] = runrecord.read(repo / "data" / "runs.jsonl")
    assert last.status == "error" and last.ai_cost == "0" and last.run_id == env.run_id
    assert last.error is not None and last.error.startswith("envelope: EnvelopeExceeded: ")
    assert "0.136" in last.error and "0.129" in last.error  # the envelope and the ceiling


def test_generation_mismatch_regenerates_counts(repo: Path, isolated_state: Path) -> None:
    from pothole.analyst import universe

    env, _, _ = env_for(repo, isolated_state, window_rows(TUESDAY))
    first = daily.run(env)
    data = repo / "data"
    day_file = dailycounts.counts_path(data, date(2026, 11, 8))
    assert json.loads((data / "state.json").read_text())["generation"] == first.generation
    day_file.unlink()
    (data / "state.json").write_text(json.dumps({"generation": "an-older-store"}))
    second = daily.run(env)
    assert json.loads((data / "state.json").read_text())["generation"] == second.generation
    assert second.generation != first.generation
    con = db.open(isolated_state / "raw.duckdb", read_only=True)
    window = counts.regenerate(con, universe.load(data / "universe.json"))
    con.close()
    got = dailycounts.read_counts(day_file)
    assert got.equals(window.filter(pc.equal(window["date"], pa.scalar(date(2026, 11, 8)))))


def test_a_days_counts_file_holds_only_that_day_and_is_never_rewritten(
    repo: Path, isolated_state: Path
) -> None:
    import gzip

    env, _, _ = env_for(repo, isolated_state, window_rows(TUESDAY))
    daily.run(env)
    path = dailycounts.counts_path(repo / "data", date(2026, 11, 8))
    before = path.read_bytes()
    assert before[4:8] == b"\0\0\0\0"  # gzip mtime 0: the same table always gives the same bytes
    lines = gzip.decompress(before).decode().splitlines()
    assert lines[0] == '"date","borough","board","complaint_type","n"'
    assert {ln.split(",")[0].strip('"') for ln in lines[1:]} == {"2026-11-08"}
    keys = [ln.split(",")[:3] for ln in lines[1:]]
    assert keys == sorted(keys)
    marker = path.stat().st_mtime_ns
    daily.run(env)  # a later load of the same day: the committed file stays
    assert path.read_bytes() == before and path.stat().st_mtime_ns == marker
    assert sorted(p.name for p in (repo / "data" / "counts").iterdir()) == ["2026-11-08.csv.gz"]


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
