"""Regression rows come only from rows that pass every check on their own."""

import random
from datetime import datetime

import pytest

from pothole.drills import catalog
from pothole.repair.validate import (
    CASE_SIZE,
    MIN_CASE,
    ToolError,
    case_day,
    case_now,
    choose_rows,
    failed,
)
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


# --- rows are chosen only among rows that pass every check on their own -------------------------


def alone_clean(spec: PipelineSpec, row: dict[str, str]) -> bool:
    """This test's own one-row engine run: no failing check and nothing raised."""
    try:
        return not failed(engine.run(spec, [row], [], case_now(case_day(spec, [row]))).checks)
    except ToolError:
        return False


def changed_alone(original: PipelineSpec, patched: PipelineSpec, row: dict[str, str]) -> bool:
    now = datetime(2100, 1, 1)
    return (
        engine.run(original, [row], [], now).table.to_pylist()
        != engine.run(patched, [row], [], now).table.to_pylist()
    )


@pytest.fixture(scope="module")
def pool(patched) -> list[dict[str, str]]:
    """Recorded rows that are clean on their own and carry a plain y coordinate."""
    rows = [dict(r) for r in catalog.clean_batch("2026-11-04")]
    good = [r for r in rows if r.get(Y) and alone_clean(patched, r)]
    assert len(good) > 60
    return good[:60]


def passes_engine(spec: PipelineSpec, chosen: list[dict[str, str]]) -> bool:
    return not failed(engine.run(spec, chosen, [], case_now(case_day(spec, chosen))).checks)


def with_comma(row: dict[str, str]) -> dict[str, str]:
    return {**row, Y: f"{int(row[Y]):,}"}


def test_two_unclean_changed_rows_are_skipped_and_one_clean_unchanged_row_fills(
    original, patched, pool
):
    changed = [with_comma(r) for r in pool[:6]]
    changed[0]["latitude"] = ""
    changed[1]["latitude"] = ""
    rows = changed + pool[6:8]
    chosen = choose_rows(original, patched, rows, [Y])
    keys = {r["unique_key"] for r in chosen}
    assert keys == {r["unique_key"] for r in changed[2:]} | {pool[6]["unique_key"]}
    assert passes_engine(patched, chosen)


def test_every_changed_row_with_an_empty_latitude_is_refused_naming_the_check(
    original, patched, pool
):
    rows = [with_comma(r) | {"latitude": ""} for r in pool[:4]] + pool[4:8]
    with pytest.raises(ToolError) as err:
        choose_rows(original, patched, rows, [Y])
    text = str(err.value)
    assert "null_rate:latitude" in text and "4" in text
    assert "passes every check on its own" in text
    assert "none of today's rows" not in text


def test_a_patch_that_changes_nothing_is_not_called_a_defect_of_the_rows(original, patched, pool):
    with pytest.raises(ToolError, match="changes none of today's rows"):
        choose_rows(original, patched, pool[:8], [Y])


def test_duplicate_keys_give_distinct_keys(original, patched, pool):
    rows = [with_comma(r) for r in pool[:4]] + [dict(pool[0])] + pool[4:8]
    rows += [{**with_comma(pool[9]), "unique_key": pool[10]["unique_key"]}, dict(pool[10])]
    chosen = choose_rows(original, patched, rows, [Y])
    assert len({r["unique_key"] for r in chosen}) == len(chosen) == CASE_SIZE


def test_a_key_that_repeats_with_differing_change_status_uses_the_row_that_changes(
    original, patched, pool
):
    # row 0 is unchanged and unclean-free; a later row with the same key changes
    twin = with_comma(pool[0])
    rows = [dict(pool[0]), twin] + pool[1:6]
    chosen = choose_rows(original, patched, rows, [Y])
    by_key = {r["unique_key"]: r for r in chosen}
    assert by_key[pool[0]["unique_key"]][Y] == twin[Y]


def test_a_changed_row_with_an_unreadable_created_date_is_skipped_not_an_error(
    original, patched, pool
):
    bad = with_comma(pool[0]) | {"created_date": "not a date"}
    rows = [bad] + [with_comma(r) for r in pool[1:3]] + pool[3:8]
    chosen = choose_rows(original, patched, rows, [Y])
    assert bad["unique_key"] not in {r["unique_key"] for r in chosen}
    assert len(chosen) == CASE_SIZE


def test_all_changed_rows_unreadable_dates_name_the_reason(original, patched, pool):
    rows = [with_comma(r) | {"created_date": "x"} for r in pool[:3]] + pool[3:8]
    with pytest.raises(ToolError, match="unreadable created_date"):
        choose_rows(original, patched, rows, [Y])


@pytest.mark.parametrize(
    ("change", "check"),
    [
        ({"agency": ""}, "required"),
        ({"borough": "ATLANTIS"}, "borough"),
        ({"closed_date": "2000-01-01T00:00:00.000"}, "closed"),
    ],
)
def test_a_row_with_its_own_defect_is_not_chosen(original, patched, pool, change, check):
    bad = with_comma(pool[0]) | change
    rows = [bad] + [with_comma(r) for r in pool[1:3]] + pool[3:8]
    chosen = choose_rows(original, patched, rows, [Y])
    assert bad["unique_key"] not in {r["unique_key"] for r in chosen}
    assert passes_engine(patched, chosen)
    assert not alone_clean(patched, bad)


def test_fewer_than_a_case_of_distinct_clean_keys_is_refused(original, patched, pool):
    rows = [with_comma(pool[0]), dict(pool[1]), dict(pool[1]), dict(pool[1]), dict(pool[1])]
    with pytest.raises(ToolError, match="distinct"):
        choose_rows(original, patched, rows, [Y])


def mutated_batch(rng: random.Random, pool) -> list[dict[str, str]]:
    rows = [dict(r) for r in rng.sample(pool, rng.randint(1, 12))]
    for row in rows:
        if rng.random() < 0.5:
            row[Y] = f"{int(row[Y]):,}"
        roll = rng.random()
        if roll < 0.12:
            row["latitude"] = ""
        elif roll < 0.22:
            row["longitude"] = ""
        elif roll < 0.30:
            row["latitude"] = "99.0"
        elif roll < 0.38:
            row[Y] = "abc"
        elif roll < 0.46:
            row["created_date"] = "not a date"
    for _ in range(rng.randint(0, 3)):
        a, b = rng.sample(range(len(rows)), 2) if len(rows) > 1 else (0, 0)
        rows[a]["unique_key"] = rows[b]["unique_key"]
    return rows


def expected_rows(original, patched, rows, edited_source) -> list[dict[str, str]] | None:
    """The oracle: its own rank, one-row runs and change test. The exact rows by input position, in
    the documented output order (by key), or None when the case must be refused."""

    def rank(i: int) -> tuple:
        row = rows[i]
        empty = sum(1 for c in patched.columns if not row.get(c.source))
        return (not row.get(edited_source), empty, row.get("unique_key", ""))

    order = sorted(range(len(rows)), key=rank)
    clean = [i for i in order if alone_clean(patched, rows[i])]
    changed = [i for i in clean if changed_alone(original, patched, rows[i])]
    if not changed or len({rows[i]["unique_key"] for i in clean}) < MIN_CASE:
        return None
    taken: dict[str, int] = {}
    for i in changed + [i for i in clean if i not in changed]:
        if rows[i]["unique_key"] not in taken and len(taken) < CASE_SIZE:
            taken[rows[i]["unique_key"]] = i
    return sorted((rows[i] for i in taken.values()), key=lambda r: r["unique_key"])


def test_property_300_random_batches_agree_with_a_brute_force_oracle(original, patched, pool):
    rng = random.Random(20261111)
    refused = chosen_count = 0
    for _ in range(300):
        rows = mutated_batch(rng, pool)
        want = expected_rows(original, patched, rows, Y)
        try:
            chosen = choose_rows(original, patched, rows, [Y])
        except ToolError:
            assert want is None or len(rows) < MIN_CASE, rows
            refused += 1
            continue
        assert want is not None
        chosen_count += 1
        assert chosen == want  # the exact rows, in the documented order
        assert len({r["unique_key"] for r in chosen}) == len(chosen)
        assert passes_engine(patched, chosen)
    assert refused > 20 and chosen_count > 20  # both outcomes were exercised


def test_a_duplicate_key_keeps_the_better_ranked_row_not_the_first_seen(original, patched, pool):
    # both rows share a key, both are clean and changed: the one with fewer empty fields is the
    # case's row, though the key set would be the same with the other
    full = with_comma(pool[0])
    sparse = full | {"descriptor": "", "location_type": ""}
    rows = [sparse] + [with_comma(r) for r in pool[1:5]] + [full]
    chosen = choose_rows(original, patched, rows, [Y])
    mine = [r for r in chosen if r["unique_key"] == full["unique_key"]]
    assert mine == [full]


def test_a_created_date_at_the_end_of_time_is_skipped_not_an_error(original, patched, pool):
    bad = with_comma(pool[0]) | {"created_date": "9999-12-31T23:00:00.000"}
    rows = [bad] + [with_comma(r) for r in pool[1:4]] + pool[4:8]
    chosen = choose_rows(original, patched, rows, [Y])
    assert bad["unique_key"] not in {r["unique_key"] for r in chosen}


def test_the_refusal_counts_use_the_real_reason_for_each_row(original, patched, pool):
    rows = [
        with_comma(pool[0]) | {"created_date": ""},
        with_comma(pool[1]) | {"created_date": "x"},
        with_comma(pool[2]) | {"created_date": "9999-12-31T23:00:00.000"},
        with_comma(pool[3]) | {"x_coordinate_state_plane": "9223372036854775808"},
    ] + pool[4:8]
    with pytest.raises(ToolError) as err:
        choose_rows(original, patched, rows, [Y])
    text = str(err.value)
    for reason in ("missing created_date", "unreadable created_date", "too late"):
        assert reason + " in 1" in text or reason in text
    assert "cast:x_coordinate_state_plane in 1" in text
