"""The regression rows are chosen by the code, first among rows the patch changes."""

import random
import time
from datetime import datetime

import pytest

from pothole.drills import catalog
from pothole.repair.validate import (
    CASE_SIZE,
    MIN_CASE,
    ToolError,
    build_case,
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


def test_51_bad_leading_rows_do_not_hide_the_clean_ones(original, patched):
    # the best-ranked changed rows carry a wrong latitude the whole batch tolerates; they are
    # unclean on their own, so the clean changed rows behind them are taken
    rows = commas(0.3)
    changed = changed_keys(original, patched, rows)
    order = [r for r in rows if r["unique_key"] in changed]
    order.sort(key=lambda r: (not r.get(Y), sum(1 for v in r.values() if not v), r["unique_key"]))
    for r in order[:51]:  # far more than any fixed cap
        r["latitude"] = "99.0"
    out = build_case(original, patched, rows, [Y])
    chosen = {r["unique_key"] for r in out.rows}
    assert len(chosen & changed) == CASE_SIZE
    assert not any(r["latitude"] == "99.0" for r in out.rows)


# --- rows are chosen only among rows that pass every check on their own -------------------------


def alone_clean(spec: PipelineSpec, row: dict[str, str]) -> bool:
    """This test's own one-row engine run: no failing check and nothing raised."""
    try:
        return not failed(engine.run(spec, [row], [], case_now(case_day(spec, [row]))).checks)
    except (ToolError, ValueError, OverflowError):
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


def expected_keys(original, patched, rows, edited_source) -> tuple[list[str], bool]:
    """The oracle: its own rank, one-row runs, change test and distinct-key count."""

    def rank(i: int) -> tuple:
        row = rows[i]
        empty = sum(1 for c in patched.columns if not row.get(c.source))
        return (not row.get(edited_source), empty, row.get("unique_key", ""))

    order = sorted(range(len(rows)), key=rank)
    clean = [i for i in order if alone_clean(patched, rows[i])]
    changed = [i for i in clean if changed_alone(original, patched, rows[i])]
    keys = {rows[i]["unique_key"] for i in clean}
    if not changed or len(keys) < MIN_CASE:
        return [], True
    taken: list[str] = []
    for i in changed + [i for i in clean if i not in changed]:
        k = rows[i]["unique_key"]
        if k not in taken and len(taken) < CASE_SIZE:
            taken.append(k)
    return taken, False


def test_property_300_random_batches_agree_with_a_brute_force_oracle(original, patched, pool):
    rng = random.Random(20261111)
    refused = chosen_count = 0
    for _ in range(300):
        rows = mutated_batch(rng, pool)
        want, must_refuse = expected_keys(original, patched, rows, Y)
        try:
            chosen = choose_rows(original, patched, rows, [Y])
        except ToolError:
            assert must_refuse or len(rows) < MIN_CASE, rows
            refused += 1
            continue
        assert not must_refuse
        chosen_count += 1
        keys = [r["unique_key"] for r in chosen]
        assert len(set(keys)) == len(keys) == len(want)
        assert set(keys) == set(want)
        assert all(alone_clean(patched, r) for r in chosen)
        assert any(changed_alone(original, patched, r) for r in chosen)
        assert passes_engine(patched, chosen)
    assert refused > 20 and chosen_count > 20  # both outcomes were exercised


def test_the_2000_row_recorded_day_returns_quickly(original, patched):
    rows = commas(0.3)
    start = time.perf_counter()
    choose_rows(original, patched, rows, [Y])
    assert time.perf_counter() - start < 3
