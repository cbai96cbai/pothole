"""A source that renamed a field is a schema change, not an outage: the rejected load is diagnosed
from a raw sample and goes to repair like any failed check. True outages stay outages."""

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from daily_kit import TUESDAY, env_for, forbid_model, run_with_fake_repair, window_rows
from store.store_fake import FakeSocrata, outage_on

from pothole import daily, runrecord
from pothole.repair.outcome import RepairOutcome
from pothole.socrata.client import SourceRejected

MISSING = "query.soql.no-such-column"


class Renamed(FakeSocrata):
    """The source renamed `old` to `new`: any query that still names `old` is rejected."""

    def __init__(self, rows: list[dict[str, str]], old: str, new: str) -> None:
        moved = [{(new if k == old else k): v for k, v in r.items()} for r in rows]
        super().__init__(moved)
        self.old = old

    def query(self, dataset: str, params: Mapping[str, str]) -> list[dict[str, Any]]:
        if params.get("$order") == ":updated_at DESC" and self.before_query:
            self.before_query(len(self.queries), params)
        named = " ".join(params.get(k, "") for k in ("$select", "$where", "$order"))
        if re.search(rf"(?<![\w:]){self.old}(?!\w)", named):
            self.queries.append(dict(params))
            raise SourceRejected(f"{dataset}: HTTP 400: No such column: {self.old}", MISSING)
        if params.get("$order") == ":updated_at DESC":  # the newest rows, whatever their day
            self.queries.append(dict(params))
            newest = sorted(self.rows, key=lambda r: r[":updated_at"], reverse=True)
            return [dict(r) for r in newest[: int(params["$limit"])]]
        return super().query(dataset, params)


def renamed_env(
    repo: Path, state: Path, old: str, new: str, ai: bool = True, **over: Any
) -> tuple[daily.Env, Any, Renamed]:
    env, sink, fake = env_for(repo, state, window_rows(TUESDAY), ai=ai, **over)
    renamed = Renamed(fake.rows, old, new)
    env.socrata = renamed
    return env, sink, renamed


def test_a_renamed_required_field_fails_the_checks_and_reaches_the_repair_agent(
    repo: Path, isolated_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[Any] = []

    def repairer(spec: Any, rows: list[dict[str, str]], failing: Any, *a: Any, **k: Any) -> Any:
        seen.append((rows, [f.name for f in failing]))
        return RepairOutcome("issue", reason="the source renamed agency")

    calls = run_with_fake_repair(monkeypatch, repairer)
    env, sink, fake = renamed_env(repo, isolated_state, "agency", "agency_code")
    record = daily.run(env)
    assert calls == [1]
    [(rows, failing)] = seen
    assert len(rows) == 20 and "agency_code" in rows[0] and "agency" not in rows[0]
    assert ":id" not in rows[0] and "schema" in failing
    assert record.status == "checks_failed" and record.rows_loaded == 200
    assert record.repair is not None and record.repair.action == "issue"
    assert [c.name for c in record.checks if c.level == "fail"][:1] == ["schema"]
    selects = {q.get("$select", "") for q in fake.queries}
    assert not any("agency" in x for x in selects)  # the load never lists the spec's fields
    assert "*, :id, :updated_at, :created_at" in selects
    assert runrecord.read(repo / "data" / "runs.jsonl") == [record]


def test_a_renamed_optional_field_is_a_warning_on_the_board_not_an_outage(
    repo: Path, isolated_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = forbid_model(monkeypatch)
    env, sink, _ = renamed_env(repo, isolated_state, "community_board", "district_board")
    record = daily.run(env)
    assert record.status == "success" and record.rows_loaded == 200 and record.repair is None
    assert calls == [] and sink.issues == [] and sink.prs == []
    [gone] = [c for c in record.checks if c.name == "schema:optional_missing"]
    assert gone.level == "warn" and "community_board" in gone.detail


def test_a_renamed_day_field_is_diagnosed_from_the_newest_rows(
    repo: Path, isolated_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[list[dict[str, str]]] = []

    def repairer(spec: Any, rows: list[dict[str, str]], *a: Any, **k: Any) -> Any:
        seen.append(rows)
        return RepairOutcome("issue")

    run_with_fake_repair(monkeypatch, repairer)
    env, _, _ = renamed_env(repo, isolated_state, "created_date", "created_on")
    record = daily.run(env)
    [rows] = seen
    assert len(rows) == 200 and "created_on" in rows[0]
    assert record.status == "checks_failed" and record.repair is not None


def test_a_schema_change_with_ai_off_files_an_issue_and_never_calls_the_model(
    repo: Path, isolated_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = forbid_model(monkeypatch)
    env, sink, _ = renamed_env(repo, isolated_state, "agency", "agency_code", ai=False)
    record = daily.run(env)
    assert calls == [] and record.status == "checks_failed"
    [issue] = sink.issues
    assert "not enabled" in issue["reason"] and [c.name for c in issue["failing"]][:1] == ["schema"]


def test_an_outage_is_still_an_outage_and_never_fetches_a_sample(
    repo: Path, isolated_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = forbid_model(monkeypatch)
    env, sink, fake = env_for(repo, isolated_state, window_rows(TUESDAY), ai=True)
    fake.before_query = outage_on(0)
    assert daily.run(env).status == "source_unavailable"
    assert calls == [] and all(not q["$select"].startswith("*") for q in fake.queries)
    assert len(sink.issues) == 1


def test_a_rejection_that_names_no_missing_column_is_an_outage(
    repo: Path, isolated_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = forbid_model(monkeypatch)
    env, sink, fake = env_for(repo, isolated_state, window_rows(TUESDAY), ai=True)

    def refuse(n: int, params: Mapping[str, str]) -> None:
        raise SourceRejected("erm2-nwe9: HTTP 403: Forbidden")

    fake.before_query = refuse
    record = daily.run(env)
    assert record.status == "source_unavailable" and calls == [] and len(sink.issues) == 1


def test_a_sample_that_cannot_be_fetched_leaves_an_outage_report(
    repo: Path, isolated_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = forbid_model(monkeypatch)
    env, sink, fake = renamed_env(repo, isolated_state, "created_date", "created_on")

    def fail_the_sample(n: int, params: Mapping[str, str]) -> None:
        if params.get("$order") == ":updated_at DESC":  # the fallback sample of the newest rows
            from pothole.socrata.client import SourceUnavailable

            raise SourceUnavailable("fake outage")

    fake.before_query = fail_the_sample
    record = daily.run(env)
    assert record.status == "source_unavailable" and calls == [] and len(sink.issues) == 1
