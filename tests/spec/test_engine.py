from datetime import datetime

import pytest
from helpers import NOW, make_row

from pothole.spec import engine
from pothole.spec.model import PipelineSpec


def run(spec: PipelineSpec, rows: list[dict[str, str]], history: list[int] | None = None):
    result = engine.run(spec, rows, history if history is not None else [10, 10, 10, 10], NOW)
    return result, {c.name: c for c in result.checks}


def test_clean_batch_passes_every_check_and_builds_the_table(spec, rows):
    result, checks = run(spec, rows)
    assert {c.level for c in result.checks} == {"pass"}
    assert result.table.num_rows == 10
    assert result.table.column("latitude")[0].as_py() == 40.6
    assert result.table.column("x_coordinate_state_plane")[0].as_py() == 1001234
    assert result.table.column("created_date")[0].as_py() == datetime(2026, 11, 9, 10, 0)
    assert result.table.column("borough")[0].as_py() == "Brooklyn"
    assert checks["schema"].detail == "all required source fields present in 10 rows"


def test_schema_unknown_field_is_a_warn(spec, rows):
    rows[0]["new_thing"] = "x"
    _, checks = run(spec, rows)
    assert checks["schema"].level == "warn"
    assert checks["schema"].detail == "1 source fields not in the spec: new_thing"


def test_schema_missing_required_field_fails(spec):
    rows = [make_row(i, agency="<omit>") for i in range(10)]
    _, checks = run(spec, rows)
    assert checks["schema"].level == "fail"
    assert checks["schema"].detail == "required source fields missing from all 10 rows: agency"


def test_cast_failures_under_one_percent_pass_over_fail(spec):
    rows = [make_row(i) for i in range(200)]
    rows[0]["latitude"] = "abc"
    result, checks = run(spec, rows, [200] * 4)
    assert checks["cast:latitude"].level == "warn"
    assert checks["cast:latitude"].detail == "latitude: 1 of 200 rows could not be read as float"
    assert result.table.column("latitude")[0].as_py() is None
    rows[1]["latitude"] = "xyz"
    rows[2]["latitude"] = "40,5"
    _, checks = run(spec, rows, [200] * 4)
    assert checks["cast:latitude"].level == "fail"
    assert checks["cast:latitude"].detail == "latitude: 3 of 200 rows could not be read as float"
    assert checks["cast:latitude"].sample_keys == ["1000", "1001", "1002"]


def test_timestamp_cast_failure_is_named(spec, rows):
    rows[0]["created_date"] = "11/09/2026"
    _, checks = run(spec, rows)
    assert checks["cast:created_date"].level == "fail"
    assert checks["cast:created_date"].detail == (
        "created_date: 1 of 10 rows could not be read as timestamp"
    )


def test_value_outside_values_fails(spec, rows):
    rows[3]["borough"] = "NEW JERSEY"
    _, checks = run(spec, rows)
    assert checks["values:borough"].level == "fail"
    assert checks["values:borough"].detail == (
        "borough: 1 of 10 rows have values outside the accepted spellings (NEW JERSEY); "
        "accepted spellings, each mapped to a standard value: "
        "BRONX, BROOKLYN, MANHATTAN, QUEENS, STATEN ISLAND, Unspecified"
    )
    assert checks["values:borough"].sample_keys == ["1003"]


def test_range_violation_pass_and_fail(spec):
    rows = [make_row(i) for i in range(100)]
    rows[0]["longitude"] = "-70.0"
    _, checks = run(spec, rows, [100] * 4)
    assert checks["range:longitude"].level == "warn"
    rows[1]["longitude"] = "0"
    _, checks = run(spec, rows, [100] * 4)
    assert checks["range:longitude"].level == "fail"
    assert checks["range:longitude"].detail == "longitude: 2 of 100 values outside [-74.27, -73.68]"


def test_null_rate_pass_and_fail(spec, rows):
    rows[0]["latitude"] = "<omit>"
    rows[0].pop("latitude")
    _, checks = run(spec, rows)
    assert checks["null_rate:latitude"].level == "pass"
    for r in rows[:3]:
        r.pop("latitude", None)
    _, checks = run(spec, rows)
    assert checks["null_rate:latitude"].level == "fail"
    assert checks["null_rate:latitude"].detail == (
        "latitude: 3 of 10 rows empty (30.0%), allowed 20.0%"
    )
    assert checks["null_rate:latitude"].sample_keys == ["1000", "1001", "1002"]


def test_freshness_pass_and_fail(spec, rows):
    _, checks = run(spec, rows)
    assert checks["freshness"].detail == "created_date: newest record is 26.0 hours old, allowed 72"
    old = [
        make_row(i, created_date="2026-11-01T10:00:00.000", closed_date="<omit>") for i in range(10)
    ]
    _, checks = run(spec, old)
    assert checks["freshness"].level == "fail"
    assert (
        checks["freshness"].detail == "created_date: newest record is 218.0 hours old, allowed 72"
    )


def test_created_not_future_pass_and_fail(spec, rows):
    _, checks = run(spec, rows)
    assert checks["created_not_future"].level == "pass"
    rows[2]["created_date"] = "2026-11-12T10:00:00.000"
    rows[2]["closed_date"] = "2026-11-13T10:00:00.000"
    _, checks = run(spec, rows)
    assert checks["created_not_future"].level == "fail"
    assert checks["created_not_future"].detail == (
        "created_date: 1 of 10 rows are dated in the future"
    )
    assert checks["created_not_future"].sample_keys == ["1002"]


def test_closed_not_before_created_pass_and_fail(spec, rows):
    _, checks = run(spec, rows)
    assert checks["closed_not_before_created"].level == "pass"
    rows[4]["closed_date"] = "2026-11-08T10:00:00.000"
    _, checks = run(spec, rows)
    assert checks["closed_not_before_created"].level == "fail"
    assert checks["closed_not_before_created"].detail == (
        "closed_date: 1 of 10 rows closed before they were created"
    )
    assert checks["closed_not_before_created"].sample_keys == ["1004"]


def test_duplicate_keys_in_batch_fail(spec, rows):
    _, checks = run(spec, rows)
    assert checks["unique_key"].level == "pass"
    rows[5]["unique_key"] = "1001"
    _, checks = run(spec, rows)
    assert checks["unique_key"].level == "fail"
    assert checks["unique_key"].detail == "unique_key: 1 keys repeat within the batch of 10 rows"
    assert checks["unique_key"].sample_keys == ["1001"]


@pytest.mark.parametrize(
    ("count", "level"), [(10, "pass"), (5, "pass"), (4, "fail"), (20, "pass"), (21, "fail")]
)
def test_volume_band(spec, count, level):
    rows = [make_row(i) for i in range(count)]
    _, checks = run(spec, rows, [10, 10, 10, 10])
    assert checks["volume"].level == level


def test_volume_message_and_empty_history(spec):
    rows = [make_row(i) for i in range(4)]
    _, checks = run(spec, rows, [10, 10, 10, 10])
    assert checks["volume"].detail == (
        "4 rows against a typical 10 over 4 earlier same-weekday counts; allowed 5 to 20"
    )
    _, checks = run(spec, rows, [])
    assert checks["volume"].level == "pass"
    assert checks["volume"].detail == "4 rows; no earlier same-weekday counts to compare"


def test_comma_thousands_number_format(spec, rows):
    spec = spec.model_copy(deep=True)
    spec.column("x_coordinate_state_plane").format = "comma_thousands"
    rows[0]["x_coordinate_state_plane"] = "1,001,234"
    result, checks = run(spec, rows)
    assert result.table.column("x_coordinate_state_plane")[0].as_py() == 1001234
    assert checks["cast:x_coordinate_state_plane"].level == "pass"


def test_required_field_missing_from_some_rows_fails(spec):
    rows = [make_row(i, agency="<omit>") if i < 9 else make_row(i) for i in range(10)]
    _, checks = run(spec, rows)
    assert checks["required:agency"].level == "fail"
    assert checks["required:agency"].detail == "agency: required but missing from 9 of 10 rows"
    assert checks["required:agency"].sample_keys == ["1000", "1001", "1002", "1003", "1004"]
    assert checks["schema"].level == "pass"


def test_required_field_present_in_every_row_passes(spec, rows):
    _, checks = run(spec, rows)
    assert checks["required:agency"].level == "pass"


def test_unknown_field_warn_is_kept_beside_a_missing_required_field(spec):
    rows = [make_row(i, agency="<omit>", extra_field="x") for i in range(10)]
    _, checks = run(spec, rows)
    assert checks["schema"].level == "fail"
    assert checks["schema:unknown_fields"].level == "warn"
    assert checks["schema:unknown_fields"].detail == "1 source fields not in the spec: extra_field"


def test_timestamps_keep_microseconds(spec, rows):
    rows[0]["created_date"] = "2026-11-09T10:00:00.123456"
    result, _ = run(spec, rows)
    assert result.table.column("created_date")[0].as_py().microsecond == 123456


def test_volume_uses_only_the_last_weeks_of_history(spec):
    rows = [make_row(i) for i in range(10)]
    _, checks = run(spec, rows, [1000, 1000, 10, 10, 10, 10])
    assert checks["volume"].level == "pass"
    assert checks["volume"].detail.endswith("over 4 earlier same-weekday counts; allowed 5 to 20")


def test_row_checks_warn_up_to_one_percent_and_fail_above():
    from pothole.spec.checks import _share_level

    assert [_share_level(n, 200) for n in (0, 1, 2, 3)] == ["pass", "warn", "warn", "fail"]


def test_closed_date_compared_at_the_minute(spec):
    rows = [make_row(i) for i in range(200)]
    rows[0].update(created_date="2026-11-09T08:30:12.000", closed_date="2026-11-09T08:30:00.000")
    _, checks = run(spec, rows, [200] * 4)
    assert checks["closed_not_before_created"].level == "pass"
    rows[0]["closed_date"] = "2026-11-09T08:28:59.000"
    _, checks = run(spec, rows, [200] * 4)
    assert checks["closed_not_before_created"].level == "warn"
    assert checks["closed_not_before_created"].detail == (
        "closed_date: 1 of 200 rows closed before they were created"
    )
    assert checks["closed_not_before_created"].sample_keys == ["1000"]


def test_created_in_the_future_warns_at_one_row_in_two_hundred(spec):
    rows = [make_row(i) for i in range(200)]
    rows[0].update(created_date="2026-11-12T10:00:00.000", closed_date="<omit>")
    rows[0].pop("closed_date", None)
    _, checks = run(spec, rows, [200] * 4)
    assert checks["created_not_future"].level == "warn"
