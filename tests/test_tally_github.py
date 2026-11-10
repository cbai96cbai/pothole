import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from pothole import cli, runrecord
from pothole.budget.tally import Tally
from pothole.tallygithub import tally_github

ROOT = Path(__file__).parents[1]


def record(run_id: str, ai: bool) -> runrecord.RunRecord:
    return runrecord.RunRecord(
        run_id, "2026-11-10T06:17:00-05:00", "2026-11-10T06:20:00-05:00", "success", 1, 1,
        ai_enabled=ai,
    )  # fmt: skip


def listing(*runs: tuple[int, str, str, str]) -> str:
    return json.dumps(
        [
            {"databaseId": i, "status": st, "conclusion": c, "createdAt": f"{day}T11:17:00Z"}
            for i, st, c, day in runs
        ]
    )


def test_ai_runs_are_admitted_once(tmp_path: Path) -> None:
    records = tmp_path / "runs.jsonl"
    for r in (record("1", True), record("2", False), record("3", True), record("5", True)):
        runrecord.append(records, r)
    out = listing(
        (1, "completed", "success", "2026-11-10"),
        (2, "completed", "success", "2026-11-10"),
        (3, "completed", "failure", "2026-11-11"),
        (4, "completed", "failure", "2026-11-11"),  # no committed record
        (5, "in_progress", "", "2026-11-12"),  # not finished
        (6, "completed", "success", "2026-11-01"),  # before --since
    )
    tally = Tally(tmp_path / "spend.jsonl")
    first = tally_github("o/r", date(2026, 11, 10), records, tally, lambda args: out)
    assert first.admitted == ["1", "3"] and first.no_record == ["4"]
    assert tally.total() == Decimal("0.128") * 2
    again = tally_github("o/r", date(2026, 11, 10), records, tally, lambda args: out)
    assert again.admitted == [] and again.already == ["1", "3"]
    assert tally.total() == Decimal("0.128") * 2


def test_daily_command_refuses_ai_without_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    result = CliRunner().invoke(cli.app, ["daily"])
    assert result.exit_code == 1 and "not set" in result.output


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
