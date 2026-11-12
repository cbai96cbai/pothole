"""`pothole demo` builds the site data from committed files alone: no key, no network."""

import json
from pathlib import Path

from typer.testing import CliRunner

from pothole import cli

REAL = Path(__file__).resolve().parents[1]
PUBLISHED = [
    "baseline-final-dev",
    "agent-final-dev-a",
    "agent-final-dev-b",
    "baseline-final-test",
    "agent-final-test",
]


def test_demo_writes_the_site_data_for_exactly_the_published_runs(tmp_path: Path) -> None:
    out = tmp_path / "data"
    result = CliRunner().invoke(cli.app, ["demo", "--repo", str(REAL), "--out", str(out)])
    assert result.exit_code == 0, result.output
    repairs = json.loads((out / "repairs.json").read_text())
    assert [s["run_id"] for s in repairs["summaries"]] == PUBLISHED
    assert "npm ci" in result.output and "npm run build" in result.output
