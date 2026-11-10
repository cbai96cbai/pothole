import pytest
from helpers import NOW, make_row

from pothole.spec import engine, ops
from pothole.spec.model import PipelineSpec


def test_map_field_changes_where_a_column_reads_from(spec):
    out = ops.apply(spec, [ops.MapField(column="agency", source="agency_name")])
    assert out.column("agency").source == "agency_name"
    assert spec.column("agency").source == "agency"
    rows = [make_row(i) for i in range(5)]
    for r in rows:
        r["agency_name"] = r.pop("agency")
    result = engine.run(out, rows, [], NOW)
    assert result.table.column("agency")[0].as_py() == "DOT"


def test_set_format_makes_a_new_date_form_readable(spec):
    out = ops.apply(spec, [ops.SetFormat(column="created_date", format="%m/%d/%Y %I:%M:%S %p")])
    rows = [make_row(i, created_date="11/09/2026 10:00:00 AM") for i in range(5)]
    result = engine.run(out, rows, [], NOW)
    assert result.table.column("created_date")[0].as_py().hour == 10
    assert {c.name: c.level for c in result.checks}["cast:created_date"] == "pass"


def test_set_format_refuses_formats_outside_the_fixed_lists(spec):
    with pytest.raises(ops.ForbiddenEdit, match="not a format for timestamp column created_date"):
        ops.apply(spec, [ops.SetFormat(column="created_date", format="%Q")])
    with pytest.raises(ops.ForbiddenEdit, match="not a format for float column latitude"):
        ops.apply(spec, [ops.SetFormat(column="latitude", format="%Y-%m-%d")])
    assert len(ops.DATE_FORMATS) == 8
    assert ops.NUMBER_FORMATS == ("plain", "comma_thousands")


def test_add_value_mapping_extends_values(spec):
    out = ops.apply(
        spec, [ops.AddValueMapping(column="borough", raw="NEW BOROUGH", value="Queens")]
    )
    assert out.column("borough").values["NEW BOROUGH"] == "Queens"
    assert "NEW BOROUGH" not in (spec.column("borough").values or {})
    with pytest.raises(ops.ForbiddenEdit, match="agency has no listed values to extend"):
        ops.apply(spec, [ops.AddValueMapping(column="agency", raw="a", value="b")])


def test_ops_cannot_touch_checks(spec):
    for column in ("checks", "source", "no_such_column"):
        with pytest.raises(ops.ForbiddenEdit, match="is not a column of this spec"):
            ops.apply(spec, [ops.SetFormat(column=column, format="plain")])
    for raw in (
        {"op": "set_check", "check": "volume", "low": 0},
        {"op": "set_format", "column": "latitude", "format": "plain", "checks": {}},
        {"op": "map_field", "column": "agency", "source": "a", "extra": 1},
        {"op": "map_field", "column": "agency"},
    ):
        with pytest.raises(ops.ForbiddenEdit):
            ops.parse(raw)
    ok = ops.parse({"op": "map_field", "column": "agency", "source": "agency_name"})
    assert ops.apply(spec, [ok]).checks == spec.checks


def test_apply_returns_a_valid_spec_and_keeps_checks(spec):
    out = ops.apply(spec, [])
    assert isinstance(out, PipelineSpec)
    assert out == spec


def test_failed_apply_leaves_the_spec_untouched(spec):
    before = spec.model_copy(deep=True)
    with pytest.raises(ops.ForbiddenEdit):
        ops.apply(
            spec,
            [ops.MapField(column="agency", source="x"), ops.MapField(column="status", source="x")],
        )
    assert spec == before


def test_map_field_to_a_source_already_in_use_is_refused(spec):
    with pytest.raises(ops.ForbiddenEdit, match="'longitude' would feed more than one column"):
        ops.apply(spec, [ops.MapField(column="latitude", source="longitude")])


def test_map_field_on_the_key_column_is_refused(spec):
    with pytest.raises(ops.ForbiddenEdit, match="unique_key is the key column"):
        ops.apply(spec, [ops.MapField(column="unique_key", source="agency")])


def test_a_swap_in_one_patch_is_valid(spec):
    out = ops.apply(
        spec,
        [
            ops.MapField(column="latitude", source="longitude"),
            ops.MapField(column="longitude", source="latitude"),
        ],
    )
    assert out.column("latitude").source == "longitude"
    assert out.column("longitude").source == "latitude"


def test_two_ops_on_one_column_apply_in_order_and_the_last_wins(spec):
    out = ops.apply(
        spec,
        [
            ops.SetFormat(column="created_date", format="%m/%d/%Y"),
            ops.SetFormat(column="created_date", format="%Y-%m-%d"),
        ],
    )
    assert out.column("created_date").format == "%Y-%m-%d"
    out = ops.apply(
        spec,
        [
            ops.AddValueMapping(column="borough", raw="X", value="Queens"),
            ops.AddValueMapping(column="borough", raw="X", value="Bronx"),
        ],
    )
    assert out.column("borough").values["X"] == "Bronx"
