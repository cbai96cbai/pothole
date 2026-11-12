import json
from pathlib import Path

import duckdb
import pytest
from investigator_kit import DAY, candidate, events_for, make_store, weather_for

from pothole.analyst import tools
from pothole.analyst.tools import Toolbox
from pothole.repair.validate import ToolError


@pytest.fixture
def box(tmp_path: Path):  # type: ignore[no-untyped-def]
    b = Toolbox(make_store(tmp_path / "raw.duckdb"), candidate(), weather_for, events_for)
    yield b
    b.close()


def test_tools_open_db_read_only_without_external_access(box: Toolbox, tmp_path: Path) -> None:
    for sql in (
        "CREATE TABLE x (a INT)",
        "DELETE FROM raw",
        "COPY raw TO 'out.csv'",
        "SELECT * FROM read_csv('/etc/hosts')",
        "SET enable_external_access = true",
        "INSTALL httpfs",
    ):
        with pytest.raises(duckdb.Error):
            box.con.execute(sql)
    assert box.con.execute("SELECT count(*) FROM raw").fetchone()[0] > 0


def test_a_statement_over_the_time_limit_is_cut_off(
    box: Toolbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tools, "STATEMENT_TIMEOUT_S", 0.2)
    with pytest.raises(ToolError, match="took longer"):
        box._query("SELECT count(*) FROM range(100000000000) a", [])
    assert box.series_rows(3)  # the connection still works afterwards


def test_every_query_is_a_constant_with_placeholders() -> None:
    sqls = [tools.SERIES_SQL, tools.POINTS_SQL, tools.NEIGHBOURS_SQL, *tools.BREAKDOWN_SQL.values()]
    for sql in sqls:
        assert "?" in sql and "BROOKLYN" not in sql and "HEAT" not in sql


def test_series_is_zero_filled_and_ends_on_the_spike_day(box: Toolbox) -> None:
    rows = box.series_rows(90)
    assert len(rows) == 90 and rows[-1] == (DAY, 30)
    assert rows[0][1] == 0 and rows[-2][1] == 3
    out = json.loads(box.run("series", {"days": 3}, 1))
    assert out == [["2026-11-05", 3], ["2026-11-06", 3], ["2026-11-07", 30]]


def test_series_days_over_ninety_is_a_tool_error(box: Toolbox) -> None:
    with pytest.raises(ToolError):
        box.run("series", {"days": 91}, 1)


def test_breakdown_counts_one_field_and_rejects_others(box: Toolbox) -> None:
    out = json.loads(box.run("breakdown", {"by": "descriptor", "top": 5}, 1))
    assert out == [["ENTIRE BUILDING", 20], ["APARTMENT ONLY", 10]]
    assert json.loads(box.run("breakdown", {"by": "hour", "top": 2}, 2))[0][1] == 2
    for bad in ({"by": "unique_key; DROP TABLE raw", "top": 3}, {"by": "status", "top": 11}):
        with pytest.raises(ToolError):
            box.run("breakdown", bad, 3)


def test_neighbours_lists_other_districts_of_the_same_type(box: Toolbox) -> None:
    out = json.loads(box.run("neighbours", {}, 1))
    assert out["this_district"] == 30
    assert out["other_districts"] == [
        {"borough": "Queens", "board": 5, "count": 8},
        {"borough": "Manhattan", "board": 12, "count": 4},
    ]


def test_points_come_from_rows_of_that_day_with_coordinates(tmp_path: Path) -> None:
    b = Toolbox(
        make_store(tmp_path / "raw.duckdb", spike=700), candidate(), weather_for, events_for
    )
    try:
        pts = b.points()
        assert len(pts) == 500 and pts == b.points()
        assert all(40.67 < lat < 40.74 and -74.0 < lon < -73.94 for lat, lon in pts)
    finally:
        b.close()


def test_a_failed_context_lookup_is_a_tool_error_not_a_crash(tmp_path: Path) -> None:
    def broken(_: object) -> object:
        raise TimeoutError("slow")

    b = Toolbox(make_store(tmp_path / "raw.duckdb"), candidate(), broken, events_for)  # type: ignore[arg-type]
    try:
        with pytest.raises(ToolError, match="unavailable"):
            b.run("weather", {}, 1)
        assert "c1" not in b.calls
        assert json.loads(b.run("events", {}, 2))[0]["name"] == "Street fair"
    finally:
        b.close()
