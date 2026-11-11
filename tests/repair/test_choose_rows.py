"""The regression rows are chosen by the code, first among rows the patch changes."""

from datetime import datetime

import pytest

from pothole.drills import catalog
from pothole.repair.validate import CASE_SIZE, ToolError, build_case, choose_rows
from pothole.spec import engine, ops
from pothole.spec.model import PipelineSpec

Y = "y_coordinate_state_plane"
COMMA = ops.parse({"op": "set_format", "column": Y, "format": "comma_thousands"})


@pytest.fixture(scope="module")
def original() -> PipelineSpec:
    return catalog.load_spec()


@pytest.fixture(scope="module")
def patched(original: PipelineSpec) -> PipelineSpec:
    return ops.apply(original, [COMMA])


def commas(share: float) -> list[dict[str, str]]:
    """A recorded day whose y coordinate carries a thousands comma on `share` of its rows."""
    rows = [dict(r) for r in catalog.clean_batch("2026-11-04")]
    every = round(1 / share)
    for i, row in enumerate(rows):
        if i % every == 0 and row.get(Y):
            row[Y] = f"{int(row[Y]):,}"
    return rows


def changed_keys(original: PipelineSpec, patched: PipelineSpec, rows: list[dict[str, str]]) -> set:
    now = datetime(2100, 1, 1)
    before = {r["unique_key"]: r for r in engine.run(original, rows, [], now).table.to_pylist()}
    after = {r["unique_key"]: r for r in engine.run(patched, rows, [], now).table.to_pylist()}
    return {k for k in after if after[k] != before[k]}


def test_the_recorded_comma_case_gets_a_case_whose_rows_change(original, patched):
    case = {c.id: c for c in catalog.load()}["comma_thousands-3"]
    rows, _ = catalog.seen_batch(case)
    out = build_case(original, patched, rows, [Y])
    assert out.expected != []
    keys = {r["unique_key"] for r in out.rows}
    assert keys <= changed_keys(original, patched, rows)


def test_when_a_third_of_the_rows_change_every_chosen_row_changes(original, patched):
    rows = commas(0.3)
    chosen = choose_rows(original, patched, rows, [Y])
    assert len(chosen) == CASE_SIZE
    assert {r["unique_key"] for r in chosen} <= changed_keys(original, patched, rows)


def test_fewer_changed_rows_than_a_case_are_filled_from_the_rest(original, patched):
    rows = commas(0.0005)  # one row in 2,000
    changed = changed_keys(original, patched, rows)
    assert 1 <= len(changed) < CASE_SIZE
    chosen = choose_rows(original, patched, rows, [Y])
    assert len(chosen) == CASE_SIZE
    assert changed <= {r["unique_key"] for r in chosen}


def test_a_patch_that_changes_no_row_is_refused_in_words_the_model_can_act_on(original, patched):
    rows = [dict(r) for r in catalog.clean_batch("2026-11-04")]
    with pytest.raises(ToolError) as err:
        build_case(original, patched, rows, [Y])
    assert "the patch changes none of today's rows, so it does not explain the failing check" in (
        str(err.value)
    )
    assert "pick" not in str(err.value)


def test_a_changed_row_with_an_unrelated_defect_is_left_out_of_the_case(original, patched):
    rows = commas(0.3)
    first = sorted(changed_keys(original, patched, rows))[0]
    # one wrong latitude in 2,000 rows only warns, but it fails a handful of rows
    ranked = choose_rows(original, patched, rows, [Y])
    victim = ranked[0]["unique_key"]
    for r in rows:
        if r["unique_key"] == victim:
            r["latitude"] = "99.0"
    assert first and victim in changed_keys(original, patched, rows)
    out = build_case(original, patched, rows, [Y])
    assert victim not in {r["unique_key"] for r in out.rows}
    assert {r["unique_key"] for r in out.rows} & changed_keys(original, patched, rows)


def test_every_changed_row_failing_is_refused_with_the_failure(original, patched):
    rows = [dict(r) for r in catalog.clean_batch("2026-11-04")]
    for i, r in enumerate(rows[:20]):
        r[Y] = f"{int(r[Y]):,}" if r.get(Y) else r.get(Y, "")
        r["latitude"] = "99.0"
    with pytest.raises(ToolError, match="fail checks under the patched spec"):
        build_case(original, patched, rows, [Y])
