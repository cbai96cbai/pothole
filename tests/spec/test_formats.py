from pothole.spec.formats import formats_that_parse
from pothole.spec.model import PipelineSpec


def test_dates_list_every_fixed_format_that_reads_them(spec: PipelineSpec):
    col = spec.column("created_date")
    assert formats_that_parse(col, ["11/09/2026 10:00:00 AM"] * 5) == ["%m/%d/%Y %I:%M:%S %p"]
    assert formats_that_parse(col, ["2026-11-09"] * 3) == ["%Y-%m-%d"]


def test_ninety_nine_percent_is_enough_and_empty_values_do_not_count(spec: PipelineSpec):
    col = spec.column("created_date")
    good = ["2026-11-09T10:00:00"] * 99
    assert "%Y-%m-%dT%H:%M:%S" in formats_that_parse(col, [*good, "oops"])
    assert formats_that_parse(col, [*good, "oops", "oops"]) == []
    assert "%Y-%m-%dT%H:%M:%S" in formats_that_parse(col, [*good, "", None])
    assert formats_that_parse(col, ["", None]) == []


def test_numbers_tell_plain_from_comma_thousands(spec: PipelineSpec):
    col = spec.column("x_coordinate_state_plane")
    assert formats_that_parse(col, ["1,234", "987,654"]) == ["comma_thousands"]
    assert formats_that_parse(col, ["1234", "987654"]) == ["plain", "comma_thousands"]
    assert formats_that_parse(col, ["12.5"]) == []
