"""Stored rows are the product of the live spec: a spec that changes what a row holds without
changing the table's shape (a format, a value mapping) rebuilds the window."""

from store_fake import NOW, FakeSocrata, make_row

from pothole.spec.model import PipelineSpec
from pothole.store import db, load


def with_status_mapping(spec: PipelineSpec, mapping: dict[str, str]) -> PipelineSpec:
    columns = [
        c.model_copy(update={"values": mapping}) if c.name == "status" else c for c in spec.columns
    ]
    return spec.model_copy(update={"columns": columns})


def statuses(con: db.Connection) -> list[str]:
    return [r[0] for r in con.execute("SELECT status FROM raw ORDER BY unique_key").fetchall()]


def test_a_value_mapping_change_rebuilds_rows_older_than_the_replay(con, spec) -> None:  # type: ignore[no-untyped-def]
    fake = FakeSocrata([make_row(0, 0), make_row(1, 300)])  # the first is 5 hours behind the cursor
    load.incremental(con, fake, spec, NOW)
    assert statuses(con) == ["Open", "Open"]
    mapped = with_status_mapping(spec, {"Open": "OPEN"})
    result = load.incremental(con, fake, mapped, NOW)
    assert statuses(con) == ["OPEN", "OPEN"] and result.rows == 2  # both read again, not replayed


def test_a_format_change_drops_the_cursor_and_records_the_new_digest(con, spec) -> None:  # type: ignore[no-untyped-def]
    load.incremental(con, FakeSocrata([make_row(0, 0)]), spec, NOW)
    old = db.get_meta(con, "spec_digest")
    changed = spec.model_copy(
        update={
            "columns": [
                c.model_copy(update={"format": "%Y-%m-%d %H:%M:%S"})
                if c.name == "closed_date"
                else c
                for c in spec.columns
            ]
        }
    )
    assert db.ensure_raw(con, changed) is True
    assert db.cursor(con) is None and db.get_meta(con, "spec_digest") != old
    assert db.ensure_raw(con, changed) is False


def test_an_unchanged_spec_does_not_rebuild(con, spec) -> None:  # type: ignore[no-untyped-def]
    fake = FakeSocrata([make_row(0, 0), make_row(1, 300)])
    load.incremental(con, fake, spec, NOW)
    again = PipelineSpec.from_yaml(spec.to_yaml())  # equal in content, a different object
    result = load.incremental(con, fake, again, NOW)
    assert result.rows == 1  # only the row inside the one-hour replay is read again


def test_a_change_to_the_checks_alone_does_not_rebuild(con, spec) -> None:  # type: ignore[no-untyped-def]
    load.incremental(con, FakeSocrata([make_row(0, 0)]), spec, NOW)
    looser = spec.model_copy(
        update={"checks": spec.checks.model_copy(update={"unique_key": False})}
    )
    assert db.ensure_raw(con, looser) is False and db.cursor(con) is not None


def test_a_store_with_no_recorded_digest_is_rebuilt_once(con, spec) -> None:  # type: ignore[no-untyped-def]
    load.incremental(con, FakeSocrata([make_row(0, 0)]), spec, NOW)
    con.execute("DELETE FROM meta WHERE key = 'spec_digest'")  # a store from before the digest
    assert db.ensure_raw(con, spec) is True and db.ensure_raw(con, spec) is False
