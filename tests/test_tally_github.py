import json
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from pothole import runrecord
from pothole.budget.tally import Tally
from pothole.tallygithub import Admitted, RunListTruncated, tally_github

ROOT = Path(__file__).parents[1]


def record(run_id: str, ai: bool, attempt: int = 1) -> runrecord.RunRecord:
    return runrecord.RunRecord(
        run_id, "2026-11-10T06:17:00-05:00", "2026-11-10T06:20:00-05:00", "success", 1, 1,
        ai_enabled=ai, attempt=attempt,
    )  # fmt: skip


def listing(*runs: tuple[int, str, str, str, str, int]) -> str:
    return json.dumps(
        [
            {
                "databaseId": i, "status": st, "conclusion": c, "event": ev, "attempt": n,
                "createdAt": f"{day}T11:17:00Z",
            }
            for i, st, c, ev, day, n in runs
        ]
    )  # fmt: skip


def charge(tmp_path: Path, out: str, *records: runrecord.RunRecord) -> tuple[Tally, Admitted]:
    path = tmp_path / "runs.jsonl"
    for r in records:
        runrecord.append(path, r)
    tally = Tally(tmp_path / "spend.jsonl")
    return tally, tally_github("o/r", date(2026, 11, 10), path, tally, lambda args: out)


@pytest.mark.parametrize("conclusion", ["success", "failure", "cancelled", "timed_out"])
def test_every_conclusion_of_a_scheduled_run_is_charged(tmp_path: Path, conclusion: str) -> None:
    out = listing((1, "completed", conclusion, "schedule", "2026-11-10", 1))
    tally, done = charge(tmp_path, out, record("1", True))
    assert done.admitted == ["1-1"] and tally.total() == Decimal("0.128")


def test_a_run_without_a_committed_record_is_charged(tmp_path: Path) -> None:
    out = listing((4, "completed", "failure", "schedule", "2026-11-11", 1))
    tally, done = charge(tmp_path, out)
    assert done.admitted == ["4-1"] and done.without_record == ["4-1"]
    assert tally.total() == Decimal("0.128")


def test_only_a_record_saying_ai_off_spares_a_scheduled_run(tmp_path: Path) -> None:
    out = listing(
        (1, "completed", "success", "schedule", "2026-11-10", 1),  # AI off in its record
        (2, "completed", "success", "workflow_dispatch", "2026-11-10", 1),  # a manual run
        (3, "in_progress", "", "schedule", "2026-11-12", 1),  # not finished
        (4, "completed", "success", "schedule", "2026-11-01", 1),  # before --since
    )
    tally, done = charge(tmp_path, out, record("1", False))
    assert done.admitted == [] and done.ai_off == ["1-1"] and tally.total() == 0


def test_each_attempt_of_a_rerun_is_its_own_charge_and_only_once(tmp_path: Path) -> None:
    out = listing((7, "completed", "success", "schedule", "2026-11-10", 3))
    path = tmp_path / "runs.jsonl"
    for r in (record("7", False, 1), record("7", True, 2)):  # attempt 3 left no record
        runrecord.append(path, r)
    tally = Tally(tmp_path / "spend.jsonl")
    first = tally_github("o/r", date(2026, 11, 10), path, tally, lambda args: out)
    assert first.admitted == ["7-2", "7-3"] and first.ai_off == ["7-1"]
    assert first.without_record == ["7-3"] and tally.total() == Decimal("0.128") * 2
    again = tally_github("o/r", date(2026, 11, 10), path, tally, lambda args: out)
    assert again.admitted == [] and again.already == ["7-2", "7-3"]
    assert tally.total() == Decimal("0.128") * 2


def test_workflow_matches_the_plan() -> None:
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "daily.yml").read_text())
    triggers = wf[True]  # yaml reads the key `on` as a boolean
    assert triggers["schedule"] == [{"cron": "17 11 * * *"}] and "workflow_dispatch" in triggers
    assert wf["concurrency"] == {"group": "daily", "cancel-in-progress": False}
    assert wf["permissions"] == {
        "contents": "write", "pull-requests": "write", "issues": "write",
        "pages": "write", "id-token": "write",
    }  # fmt: skip
    assert "github.event_name == 'schedule'" in wf["env"]["AI_ENABLED"]
    text = (ROOT / ".github" / "workflows" / "daily.yml").read_text()
    assert "key: raw-v1-${{ github.run_id }}-${{ github.run_attempt }}" in text
    assert "41898282+github-actions[bot]@users.noreply.github.com" in text
    deploy = wf["jobs"]["deploy"]
    assert deploy["if"] == "${{ vars.PAGES_ENABLED == 'true' }}"
    run_step = next(s for s in wf["jobs"]["run"]["steps"] if s.get("name") == "Daily run")
    for name in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY"):
        assert run_step["env"][name].startswith("${{ github.event_name == 'schedule' && secrets.")
    commit = next(
        s for s in wf["jobs"]["run"]["steps"] if s.get("name") == "Commit data and results"
    )
    script = commit["run"]
    assert script.index("git config user.name") < script.index("git commit")
    assert script.index("git config user.email") < script.index("git pull --rebase")
    assert script.count("git pull --rebase") == 2 and script.count("git push") == 2


def paged_gh(total: int, limits: list[int]) -> "Callable[[list[str]], str]":
    """A `gh` that has `total` scheduled runs, newest first, and returns at most --limit of them."""
    runs = [
        {
            "databaseId": 1000 - i, "status": "completed", "conclusion": "success",
            "event": "schedule", "attempt": 1, "createdAt": "2026-11-10T11:17:00Z",
        }
        for i in range(total)
    ]  # fmt: skip

    def gh(args: list[str]) -> str:
        limit = int(args[args.index("--limit") + 1])
        limits.append(limit)
        assert args[args.index("--created") + 1] == ">=2026-11-10"
        return json.dumps(runs[:limit])

    return gh


def test_a_listing_longer_than_one_page_is_counted_in_full(tmp_path: Path) -> None:
    limits: list[int] = []
    path = tmp_path / "runs.jsonl"
    for i in range(450):  # every run has a committed record saying AI was off: none is charged
        runrecord.append(path, record(str(1000 - i), False))
    tally = Tally(tmp_path / "spend.jsonl")
    done = tally_github("o/r", date(2026, 11, 10), path, tally, paged_gh(450, limits))
    assert len(done.ai_off) == 450 and limits == [200, 400, 800]  # asked again until not full


def test_a_listing_that_may_be_cut_short_stops_the_tally_instead_of_under_counting(
    tmp_path: Path,
) -> None:
    tally = Tally(tmp_path / "spend.jsonl")
    with pytest.raises(RunListTruncated, match="older runs may be missing"):
        tally_github("o/r", date(2026, 11, 10), tmp_path / "runs.jsonl", tally, paged_gh(10**5, []))
    assert tally.total() == 0  # nothing was charged from a list that might be incomplete
