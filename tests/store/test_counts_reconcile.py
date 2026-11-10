from datetime import date

from store_fake import NOW, FakeSocrata, make_row

from pothole.analyst.universe import Universe
from pothole.spec.model import PipelineSpec
from pothole.store import counts, db, load, reconcile


def test_counts_regenerated_match_rows(con, spec: PipelineSpec) -> None:
    rows = [
        make_row(0, 0),
        make_row(1, 1),
        make_row(2, 2, complaint_type="Noise", community_board="03 QUEENS"),
        make_row(3, 3, created="2026-11-09T09:00:00.000"),
        make_row(4, 4, community_board="Unspecified BROOKLYN"),  # outside the 59 districts
        make_row(5, 5, community_board="13 BROOKLYN"),  # Brooklyn has 18, 13 is regular
    ]
    load.incremental(con, FakeSocrata(rows), spec, NOW)
    table = counts.regenerate(con, None).to_pylist()
    got = {(r["date"], r["borough"], r["board"], r["complaint_type"]): r["n"] for r in table}
    d8, d9 = date(2026, 11, 8), date(2026, 11, 9)
    assert got == {
        (d8, "Brooklyn", 2, "Street Condition"): 2,
        (d8, "Queens", 3, "Noise"): 1,
        (d9, "Brooklyn", 2, "Street Condition"): 1,
        (d8, "Brooklyn", 13, "Street Condition"): 1,
    }
    assert sum(got.values()) == 5  # the Unspecified row is out
    only = Universe(frozenset({"Noise"}), frozenset({("Queens", 3)}))
    assert [r["n"] for r in counts.regenerate(con, only).to_pylist()] == [1]
    assert counts.vintage(con, date(2026, 11, 8)) == [(date(2026, 11, 8), 5)]


def test_reconcile_deletes_and_adds(con, spec: PipelineSpec) -> None:
    rows = [make_row(i, i) for i in range(4)] + [
        make_row(10, 10, created="2026-11-07T08:00:00.000")
    ]
    fake = FakeSocrata(rows)
    load.incremental(con, fake, spec, NOW)
    fake.rows.pop(0)  # upstream deleted 1000 (on 11-08)
    fake.rows.append(make_row(20, 20, created="2026-11-07T11:00:00.000"))  # never read here
    result = reconcile.weekly(con, fake, spec, NOW)
    assert (result.days_fixed, result.deleted, result.added) == (2, 1, 1)
    assert [r[0] for r in con.execute("SELECT unique_key FROM raw ORDER BY 1").fetchall()] == [
        "1001",
        "1002",
        "1003",
        "1010",
        "1020",
    ]
    assert result.days_checked == 2
    rows_asked = [q["$select"] for q in fake.queries if q["$select"].startswith("*")]
    assert rows_asked and set(rows_asked) == {db.SELECT_ALL}  # whole rows, never a field list
    again = reconcile.weekly(con, fake, spec, NOW)
    assert (again.days_fixed, again.deleted, again.added) == (0, 0, 0)
    assert db.get_meta(con, "last_success") is not None
