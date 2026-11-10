"""A regression case guards the transform it was written for, whatever the live spec becomes."""

import json
from typing import Any

from pothole.repair import render
from pothole.repair.outcome import RegressionCase, table_rows
from pothole.repair.validate import case_reproduces
from pothole.spec import engine, ops
from pothole.spec.model import PipelineSpec
from pothole.spec.ops import MapField


def written_for(spec: PipelineSpec, failing: Any, field: str) -> dict[str, Any]:
    """A case file for a rename of `agency` to `field`, read back as the collector reads it."""
    renamed = [{(field if k == "agency" else k): v for k, v in r.items()} for r in failing.rows[:4]]
    patched = ops.apply(spec, [MapField(column="agency", source=field)])
    expected = table_rows(engine.run(patched, renamed, [], failing.now).table)
    return json.loads(render.regression_file(RegressionCase(renamed, expected), patched))


def test_two_successive_renames_of_one_column_both_keep_passing(spec, all_batches):
    first = written_for(spec, all_batches[0], "agency_code")  # merged first
    second_base = ops.apply(spec, [MapField(column="agency", source="agency_code")])
    second = written_for(
        second_base, all_batches[0], "agency_id"
    )  # merged later; the live spec moved on
    assert case_reproduces(first) and case_reproduces(second)


def test_a_case_names_no_file_it_carries_its_spec(spec, all_batches):
    data = written_for(spec, all_batches[0], "agency_code")
    assert (
        data["spec"] == ops.apply(spec, [MapField(column="agency", source="agency_code")]).to_yaml()
    )
