from datetime import datetime, timedelta
from pathlib import Path

import pytest
from store_fake import FakeSocrata, make_row, outage_on
from typer.testing import CliRunner

from pothole import cli
from pothole.store import db

PINNED_SPEC = Path(__file__).parents[2] / "fixtures" / "batches" / "spec.yaml"


def recent_rows() -> list[dict[str, str]]:
    day = (datetime.now(cli.NEW_YORK) - timedelta(days=1)).strftime("%Y-%m-%dT10:00:00.000")
    return [make_row(i, i, created=day) for i in range(3)]


def test_load_command_loads_into_the_state_folder(
    isolated_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeSocrata(recent_rows())
    monkeypatch.setattr(cli, "Socrata", lambda *a, **k: fake)
    result = CliRunner().invoke(cli.app, ["load", "--spec", str(PINNED_SPEC)])
    assert result.exit_code == 0, result.output
    assert "rows 3 pages 1" in result.output
    assert "pass unique_key" in result.output
    con = db.open(isolated_state / "raw.duckdb", read_only=True)
    assert con.execute("SELECT count(*) FROM raw").fetchone()[0] == 3
    con.close()


def test_load_command_exits_1_when_the_source_is_down(
    isolated_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeSocrata(recent_rows())
    fake.before_query = outage_on(0)
    monkeypatch.setattr(cli, "Socrata", lambda *a, **k: fake)
    result = CliRunner().invoke(cli.app, ["load", "--spec", str(PINNED_SPEC)])
    assert result.exit_code == 1
    assert "load stopped" in result.output
