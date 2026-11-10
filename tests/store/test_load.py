from pathlib import Path

import pytest
from store_fake import NOW, FakeSocrata, make_row, outage_on, stamp

from pothole.socrata.client import SourceUnavailable
from pothole.spec.model import PipelineSpec
from pothole.store import db, load


def keys(con: db.Connection) -> list[str]:
    return [r[0] for r in con.execute("SELECT unique_key FROM raw ORDER BY 1").fetchall()]


def test_ties_on_updated_at_are_not_skipped(con, spec: PipelineSpec) -> None:
    fake = FakeSocrata([make_row(i, 10) for i in range(5)])
    result = load.incremental(con, fake, spec, NOW, page_size=2)
    assert keys(con) == [str(1000 + i) for i in range(5)]
    assert (result.rows, result.pages) == (5, 3)
    assert result.cursor == (stamp(10), "row-00004")


def test_rows_updated_during_paging_wait_for_next_run(con, spec: PipelineSpec) -> None:
    fake = FakeSocrata([make_row(i, i) for i in range(3)])

    def hook(count: int, params) -> None:  # type: ignore[no-untyped-def]
        if count == 1:  # after the max(:updated_at) query, before the first page
            fake.rows.append(make_row(9, 500))
            fake.rows[0][":updated_at"] = stamp(600)

    fake.before_query = hook
    load.incremental(con, fake, spec, NOW, page_size=2)
    assert keys(con) == ["1001", "1002"]  # the new row and the re-stamped row wait
    load.incremental(con, fake, spec, NOW, page_size=2)
    assert keys(con) == ["1000", "1001", "1002", "1009"]


def test_late_visible_row_with_old_timestamp_is_read_by_overlap(con, spec: PipelineSpec) -> None:
    fake = FakeSocrata([make_row(0, 10), make_row(1, 200)])
    load.incremental(con, fake, spec, NOW)
    fake.rows.append(make_row(2, 160))  # visible late, stamped 40 minutes before the cursor
    load.incremental(con, fake, spec, NOW)
    assert "1002" in keys(con)


def test_row_older_than_the_overlap_is_not_read(con, spec: PipelineSpec) -> None:
    fake = FakeSocrata([make_row(0, 10), make_row(1, 200)])
    load.incremental(con, fake, spec, NOW)
    fake.rows.append(make_row(2, 100))  # 100 minutes before the cursor
    load.incremental(con, fake, spec, NOW)
    assert "1002" not in keys(con)


def test_partial_load_does_not_set_last_success(con, spec: PipelineSpec) -> None:
    fake = FakeSocrata([make_row(i, i) for i in range(5)])
    fake.before_query = outage_on(2)  # max query, page 1, then page 2 fails
    with pytest.raises(SourceUnavailable):
        load.incremental(con, fake, spec, NOW, page_size=2)
    assert db.get_meta(con, "last_success") is None
    assert db.get_meta(con, "generation") is None
    load.incremental(con, FakeSocrata(fake.rows), spec, NOW, page_size=2)
    assert db.get_meta(con, "last_success") == "2026-11-10T12:00:00"


def test_late_correction_updates_old_row(con, spec: PipelineSpec) -> None:
    fake = FakeSocrata([make_row(0, 10), make_row(1, 20)])
    load.incremental(con, fake, spec, NOW)
    fake.rows[0].update({"status": "Closed", ":updated_at": stamp(30)})
    load.incremental(con, fake, spec, NOW)
    row = con.execute("SELECT status, _updated_at FROM raw WHERE unique_key = '1000'").fetchone()
    assert row[0] == "Closed" and row[1].minute == 30
    assert con.execute("SELECT count(*) FROM raw").fetchone()[0] == 2


def test_first_arrival_time_is_kept_on_update(con, spec: PipelineSpec) -> None:
    fake = FakeSocrata([make_row(0, 10)])
    load.incremental(con, fake, spec, NOW)
    fake.rows[0].update({":updated_at": stamp(30), ":created_at": stamp(29)})
    load.incremental(con, fake, spec, NOW)
    created = con.execute("SELECT _created_at FROM raw").fetchone()[0]
    assert created.minute == 9


def test_crash_between_pages_resumes_from_last_page(con, spec: PipelineSpec) -> None:
    fake = FakeSocrata([make_row(i, 60 * i) for i in range(5)])
    fake.before_query = outage_on(3)  # max query, page 1, page 2, then page 3 fails
    with pytest.raises(SourceUnavailable):
        load.incremental(con, fake, spec, NOW, page_size=2)
    assert db.cursor(con) == (stamp(180), "row-00003")
    assert keys(con) == ["1000", "1001", "1002", "1003"]
    resumed = FakeSocrata(fake.rows)
    load.incremental(con, resumed, spec, NOW, page_size=2)
    assert keys(con) == [str(1000 + i) for i in range(5)]


def test_resume_after_crash_does_not_rewind(con, spec: PipelineSpec) -> None:
    fake = FakeSocrata([make_row(i, 60 * i) for i in range(5)])
    fake.before_query = outage_on(3)
    with pytest.raises(SourceUnavailable):
        load.incremental(con, fake, spec, NOW, page_size=2)
    assert db.get_meta(con, "load_in_progress") == "1"
    resumed = FakeSocrata(fake.rows)
    result = load.incremental(con, resumed, spec, NOW, page_size=2)
    first_page = resumed.queries[1]["$where"]
    assert f":updated_at > '{stamp(180)}'" in first_page and "'row-00003'" in first_page
    assert result.rows == 1  # only the row after the last committed page
    assert db.get_meta(con, "load_in_progress") is None
    again = FakeSocrata(fake.rows)  # a finished load rewinds again
    load.incremental(con, again, spec, NOW, page_size=2)
    assert f":updated_at >= '{stamp(180)}'" in again.queries[1]["$where"]  # 240 minus one hour


def test_outage_mid_paging_keeps_cursor(con, spec: PipelineSpec) -> None:
    fake = FakeSocrata([make_row(i, i) for i in range(6)])
    load.incremental(con, fake, spec, NOW, page_size=10)
    fake.rows += [make_row(10 + i, 200 + i) for i in range(6)]
    fake.before_query = outage_on(len(fake.queries) + 2)  # the max query, page 1, then page 2
    with pytest.raises(SourceUnavailable):
        load.incremental(con, fake, spec, NOW, page_size=10)
    assert db.cursor(con) == (stamp(203), "row-00013")  # the replayed 6 rows plus 4 new ones
    assert len(keys(con)) == 10
    assert db.get_meta(con, "last_success") == "2026-11-10T12:00:00"  # the earlier run's


def test_reload_is_bounded_and_idempotent(con, spec: PipelineSpec) -> None:
    window = [make_row(i, i * 10) for i in range(30)]
    old = [make_row(40 + i, i, created="2026-07-01T10:00:00.000") for i in range(5)]
    fake = FakeSocrata(window + old)
    first = load.incremental(con, fake, spec, NOW, page_size=7)
    sql = "SELECT * EXCLUDE (_updated_at) FROM raw ORDER BY unique_key"
    snapshot = con.execute(sql).fetchall()
    for n, row in enumerate(fake.rows):  # a dataset reload restamps every row
        row[":updated_at"] = stamp(1000 + n)
    second = load.incremental(con, fake, spec, NOW, page_size=7)
    assert first.rows == 30
    assert second.rows == 30  # one window of rows, none of the old ones
    assert con.execute(sql).fetchall() == snapshot
    assert len(snapshot) == 30
    assert second.generation != first.generation
    third = load.incremental(con, FakeSocrata(fake.rows), spec, NOW, page_size=7)
    assert third.rows == 30  # the restamps all fall inside the hour before the cursor


def test_cache_miss_rebuilds_window(tmp_path: Path, spec: PipelineSpec) -> None:
    rows = [make_row(i, i) for i in range(5)]
    rows.append(make_row(7, 7, created="2026-07-01T10:00:00.000"))  # older than 90 days
    path = tmp_path / "cache" / "raw.duckdb"
    with_cache = db.open(path)
    load.incremental(with_cache, FakeSocrata(rows), spec, NOW)
    before = with_cache.execute("SELECT * FROM raw ORDER BY unique_key").fetchall()
    with_cache.close()
    path.unlink()
    rebuilt = db.open(path)
    fake = FakeSocrata(rows)
    result = load.incremental(rebuilt, fake, spec, NOW)
    assert result.rows == 5  # the window filter keeps the old row out of the read
    assert "created_date >= '2026-08-12T00:00:00'" in fake.queries[1]["$where"]
    assert "1970-01-01" in fake.queries[1]["$where"]
    assert rebuilt.execute("SELECT * FROM raw ORDER BY unique_key").fetchall() == before
    rebuilt.close()


def test_rows_older_than_the_window_are_pruned_after_success(con, spec: PipelineSpec) -> None:
    fake = FakeSocrata([make_row(0, 0, created="2026-08-12T00:00:00.000")])
    load.incremental(con, fake, spec, NOW)
    assert keys(con) == ["1000"]
    later = NOW.replace(day=11)  # the window start moves on a day
    fake.rows.append(make_row(1, 5))
    load.incremental(con, fake, spec, later)
    assert keys(con) == ["1001"]


def test_changed_spec_columns_rebuild_the_table(con, spec: PipelineSpec) -> None:
    fake = FakeSocrata([make_row(0, 0)])
    load.incremental(con, fake, spec, NOW)
    fewer = spec.model_copy(update={"columns": spec.columns[:-1]})
    assert db.cursor(con) is not None
    assert db.ensure_raw(con, fewer) is True
    assert db.cursor(con) is None
    assert db.ensure_raw(con, fewer) is False


def test_checks_keep_the_worst_page_and_window_wide_results(con, spec: PipelineSpec) -> None:
    rows = [make_row(i, i, created="2026-11-08T10:00:00.000") for i in range(4)]
    rows[3]["status"] = ""  # a required value missing on the second page only
    result = load.incremental(con, FakeSocrata(rows), spec, NOW, page_size=2)
    by_name = {c.name: c for c in result.checks}
    assert by_name["required:status"].level == "fail"
    assert by_name["freshness"].level == "pass"
    assert by_name["volume"].level == "pass"  # no earlier same-weekday counts yet


class _FailOnSecond:
    """Wraps a db function so its second call fails after the real work is done."""

    def __init__(self, real):  # type: ignore[no-untyped-def]
        self.real, self.calls = real, 0

    def __call__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        self.calls += 1
        out = self.real(*args, **kwargs)
        if self.calls == 2:
            raise RuntimeError("injected")
        return out


@pytest.mark.parametrize("name", ["upsert", "set_cursor"])
def test_failure_inside_a_page_leaves_cursor_and_rows_unchanged(
    con, spec: PipelineSpec, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    fake = FakeSocrata([make_row(i, i) for i in range(6)])
    monkeypatch.setattr(db, name, _FailOnSecond(getattr(db, name)))
    with pytest.raises(RuntimeError, match="injected"):
        load.incremental(con, fake, spec, NOW, page_size=2)
    assert db.cursor(con) == (stamp(1), "row-00001")
    assert keys(con) == ["1000", "1001"]
    assert db.get_meta(con, "last_success") is None


def test_quotes_in_an_id_and_a_timestamp_are_escaped(con, spec: PipelineSpec) -> None:
    where = load.keyset_where("T'x", "row-a'b", "W'w", "S'")
    assert where == (
        "((:updated_at > 'T''x') OR (:updated_at = 'T''x' AND :id > 'row-a''b')) "
        "AND :updated_at <= 'W''w' AND created_date >= 'S'''"
    )
    rows = [make_row(i, i, **{":id": f"row-a'b{i}"}) for i in range(3)]
    load.incremental(con, FakeSocrata(rows), spec, NOW, page_size=1)  # ids reach the next query
    assert keys(con) == ["1000", "1001", "1002"]
    assert con.execute("SELECT _id FROM raw ORDER BY 1").fetchone()[0] == "row-a'b0"


def test_rebuilding_the_table_is_one_transaction(con, spec: PipelineSpec) -> None:
    load.incremental(con, FakeSocrata([make_row(0, 0)]), spec, NOW)
    fewer = spec.model_copy(update={"columns": spec.columns[:-1]})

    class Spy:
        def __init__(self, inner: db.Connection) -> None:
            self.inner = inner

        def execute(self, sql: str, *args):  # type: ignore[no-untyped-def]
            if sql.startswith("CREATE TABLE"):
                raise RuntimeError("injected")
            return self.inner.execute(sql, *args)

        def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
            return getattr(self.inner, name)

    with pytest.raises(RuntimeError, match="injected"):
        db.ensure_raw(Spy(con), fewer)  # type: ignore[arg-type]
    assert keys(con) == ["1000"]  # the old table and its cursor are still there
    assert db.cursor(con) is not None


def test_the_load_asks_for_every_field_not_a_list_of_the_specs(con, spec: PipelineSpec) -> None:
    fake = FakeSocrata([make_row(0, 0)])
    load.incremental(con, fake, spec, NOW)
    pages = [q["$select"] for q in fake.queries if "max(" not in q["$select"]]
    assert pages == ["*, :id, :updated_at, :created_at"]
