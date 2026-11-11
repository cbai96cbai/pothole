import json
from pathlib import Path

import pytest

from pothole.drills import compare


def write(root: Path, name: str, rows: list[tuple[str, str, str, bool]]) -> None:
    out = root / name
    out.mkdir(parents=True)
    with (out / "verdicts.jsonl").open("w") as fh:
        for case_id, family, expected, ok in rows:
            fh.write(json.dumps({"id": case_id, "family": family, "expected": expected,
                                 "correct": ok}) + "\n")  # fmt: skip


def runs(root: Path, families: int, per: int, a_wins: int) -> None:
    """The agent repairs about `a_wins` of every family's `per` cases (one more or fewer by family);
    the baseline repairs none."""
    a, b = [], []
    for f in range(families):
        for i in range(per):
            cid = f"f{f}-{i}"
            a.append((cid, f"f{f}", "patch", i < a_wins + (f % 3 - 1)))
            b.append((cid, f"f{f}", "patch", False))
    for tag, rows in (("agent", a), ("base", b)):
        extra = [("iss-0", "fd", "issue", tag == "agent"), ("nc-0", "sp", "no_change", True)]
        write(root, tag, rows + extra)


def test_a_known_difference_is_recovered_inside_its_interval(tmp_path):
    runs(tmp_path, families=12, per=6, a_wins=4)  # agent 4 of 6 everywhere, baseline 0
    body = compare.compare(tmp_path / "agent", tmp_path / "base")
    m = body["repair_success"]
    assert m["difference"] == pytest.approx(4 / 6, abs=1e-4) and m["cases"] == 72
    low, high = m["interval_95"]
    assert low < m["difference"] < high and high - low < 0.3
    assert body["issue_accuracy"]["difference"] == 1.0
    assert body["no_change_accuracy"]["difference"] == 0.0
    assert "note" not in body and body["seed"] == 7 and body["resamples"] == 10_000


def test_the_interval_follows_how_much_families_differ(tmp_path):
    # the agent wins every case in half the families and none in the others: wide interval
    a, b = [], []
    for f in range(12):
        for i in range(6):
            a.append((f"f{f}-{i}", f"f{f}", "patch", f % 2 == 0))
            b.append((f"f{f}-{i}", f"f{f}", "patch", False))
    write(tmp_path, "agent", a)
    write(tmp_path, "base", b)
    m = compare.compare(tmp_path / "agent", tmp_path / "base")["repair_success"]
    low, high = m["interval_95"]
    assert m["difference"] == 0.5 and low < 0.35 and high > 0.65


def test_few_families_are_said_to_be_wide(tmp_path):
    runs(tmp_path, families=3, per=6, a_wins=5)
    body = compare.compare(tmp_path / "agent", tmp_path / "base")
    assert "only 3 families" in body["note"] and "wide" in body["note"]
    assert "only 3 families" in compare.render(body)


def test_only_cases_both_runs_scored_are_paired(tmp_path):
    runs(tmp_path, families=3, per=2, a_wins=2)
    (tmp_path / "base" / "verdicts.jsonl").write_text(
        "".join(
            line + "\n"
            for line in (tmp_path / "base" / "verdicts.jsonl").read_text().splitlines()[:-1]
        )
    )
    body = compare.compare(tmp_path / "agent", tmp_path / "base")
    assert body["unpaired"] == {"a_only": 1, "b_only": 0}
    assert body["no_change_accuracy"] is None


def test_runs_with_nothing_in_common_are_an_error(tmp_path):
    write(tmp_path, "agent", [("x", "f", "patch", True)])
    write(tmp_path, "base", [("y", "f", "patch", True)])
    with pytest.raises(ValueError, match="no cases"):
        compare.compare(tmp_path / "agent", tmp_path / "base")
