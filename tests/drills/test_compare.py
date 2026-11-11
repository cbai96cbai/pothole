import json
from pathlib import Path

import pytest

from pothole.drills import compare


def cmp(root: Path) -> dict:
    """Compare agent with base, taking the agent's case ids as the split's catalog ids."""
    ids = {
        json.loads(x)["id"] for x in (root / "agent" / "verdicts.jsonl").read_text().splitlines()
    }
    return compare.compare(root / "agent", root / "base", case_ids=lambda split: ids)


def write(
    root: Path, name: str, rows: list[tuple[str, str, str, bool]], split: str = "dev",
    summary: bool = True,
) -> None:  # fmt: skip
    out = root / name
    out.mkdir(parents=True)
    if summary:
        (out / "summary.json").write_text(json.dumps({"split": split, "cases": len(rows)}))
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
    body = cmp(tmp_path)
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
    m = cmp(tmp_path)["repair_success"]
    low, high = m["interval_95"]
    assert m["difference"] == 0.5 and low < 0.35 and high > 0.65


def test_few_families_are_said_to_be_wide(tmp_path):
    runs(tmp_path, families=3, per=6, a_wins=5)
    body = cmp(tmp_path)
    assert "only 3 families" in body["note"] and "wide" in body["note"]
    assert "only 3 families" in compare.render(body)


def test_equal_counts_with_a_wrong_id_is_refused_naming_the_run_and_the_id(tmp_path):
    runs(tmp_path, families=3, per=2, a_wins=2)
    base = tmp_path / "base" / "verdicts.jsonl"
    lines = base.read_text().splitlines()
    lines[0] = lines[0].replace('"f0-0"', '"other"')  # same size, one case differs
    base.write_text("\n".join(lines) + "\n")
    with pytest.raises(ValueError, match=r"base .*\bf0-0\b|base .*\bother\b"):
        cmp(tmp_path)


def test_a_missing_id_is_named(tmp_path):
    runs(tmp_path, families=2, per=2, a_wins=1)
    base = tmp_path / "base" / "verdicts.jsonl"
    base.write_text("\n".join(base.read_text().splitlines()[1:]) + "\n")
    with pytest.raises(ValueError, match=r"base.*missing f0-0"):
        cmp(tmp_path)


def test_an_extra_id_is_named(tmp_path):
    runs(tmp_path, families=2, per=2, a_wins=1)
    with (tmp_path / "base" / "verdicts.jsonl").open("a") as fh:
        fh.write(json.dumps({"id": "zzz", "family": "f", "expected": "patch", "correct": True}))
    with pytest.raises(ValueError, match=r"base.*not in the split: zzz"):
        cmp(tmp_path)


def test_a_duplicated_id_is_refused_naming_it(tmp_path):
    runs(tmp_path, families=2, per=2, a_wins=1)
    agent = tmp_path / "agent" / "verdicts.jsonl"
    lines = agent.read_text().splitlines()
    agent.write_text("\n".join([*lines, lines[0]]) + "\n")
    with pytest.raises(ValueError, match=r"agent.*twice: f0-0"):
        cmp(tmp_path)


def test_the_catalog_decides_what_the_split_holds(tmp_path):
    runs(tmp_path, families=2, per=2, a_wins=1)  # ids that are not the dev split's cases
    with pytest.raises(ValueError, match="agent"):
        compare.compare(tmp_path / "agent", tmp_path / "base")


def test_the_two_real_final_test_runs_still_compare():
    results = Path(__file__).parents[2] / "results"
    body = compare.compare(results / "agent-final-test", results / "baseline-final-test")
    assert body["paired_cases"] == 30
    assert body["unpaired"] == {"a_only": 0, "b_only": 0}


def test_an_unfinished_run_is_refused(tmp_path):
    runs(tmp_path, families=2, per=2, a_wins=1)
    (tmp_path / "base" / "summary.json").unlink()
    with pytest.raises(ValueError, match="no summary"):
        cmp(tmp_path)


def test_a_run_with_missing_cases_is_refused(tmp_path):
    runs(tmp_path, families=2, per=2, a_wins=1)
    base = tmp_path / "base" / "verdicts.jsonl"
    base.write_text("\n".join(base.read_text().splitlines()[:-1]) + "\n")
    with pytest.raises(ValueError, match="incomplete"):
        cmp(tmp_path)


def test_runs_on_different_splits_are_refused(tmp_path):
    rows = [("x", "f", "patch", True)]
    write(tmp_path, "agent", rows, split="dev")
    write(tmp_path, "base", rows, split="test")
    with pytest.raises(ValueError, match="different splits"):
        cmp(tmp_path)
