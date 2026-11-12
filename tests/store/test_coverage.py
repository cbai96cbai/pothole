from datetime import date, datetime, timedelta
from pathlib import Path

from store_fake import NOW, FakeSocrata, make_row

from pothole.spec.model import PipelineSpec
from pothole.store import coverage, db, load


def test_a_finished_load_records_the_first_day_the_store_holds(
    con: db.Connection, spec: PipelineSpec
) -> None:
    load.incremental(con, FakeSocrata([make_row(0, 10)]), spec, NOW)
    first = NOW.date() - timedelta(days=spec.source.window_days)
    held = coverage.of(con, spec.source.window_days)
    assert held == coverage.Coverage(first, NOW.date())
    assert db.get_meta(con, "window_start") == first.isoformat()


def test_the_recorded_start_wins_over_a_changed_window_length(
    con: db.Connection, spec: PipelineSpec
) -> None:
    load.incremental(con, FakeSocrata([make_row(0, 10)]), spec, NOW)
    recorded = coverage.of(con, 90)
    assert coverage.of(con, 30) == recorded


def test_a_store_loaded_before_the_record_existed_derives_it_from_the_load_date(
    con: db.Connection,
) -> None:
    db.set_meta(con, "last_success", datetime(2026, 11, 11, 15, 56).isoformat())
    assert coverage.of(con, 90) == coverage.Coverage(date(2026, 8, 13), date(2026, 11, 11))


def test_a_store_never_loaded_has_no_coverage(con: db.Connection) -> None:
    assert coverage.of(con, 90) is None


def test_the_boundary_is_one_function(tmp_path: Path) -> None:
    assert coverage.window_start(date(2026, 11, 11), 90) == date(2026, 8, 13)
