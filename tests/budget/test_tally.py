import threading
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from pothole.budget.tally import (
    CUTOFF,
    BudgetExceeded,
    CutoffPassed,
    Tally,
    settle_if_complete,
)

BEFORE = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


def tally(tmp_path: Path, now: datetime = BEFORE) -> Tally:
    return Tally(tmp_path / "spend.jsonl", clock=lambda: now)


def test_tally_refuses_past_25(tmp_path: Path) -> None:
    t = tally(tmp_path)
    t.admit("a", Decimal("20"), github=True)
    t.admit("b", Decimal("5"), github=True)  # exactly at the cap is allowed
    with pytest.raises(BudgetExceeded):
        t.admit("c", Decimal("0.001"), github=True)
    assert t.total() == Decimal("25")


def test_tally_reserves_3_for_github_runs(tmp_path: Path) -> None:
    t = tally(tmp_path)
    t.admit("local", Decimal("22"))  # exactly 25 - 3 is allowed
    with pytest.raises(BudgetExceeded):
        t.admit("local2", Decimal("0.001"))
    t.admit("gh", Decimal("3"), github=True)  # the reserve is for GitHub
    assert t.total() == Decimal("25")


def test_cutoff_refuses_after_2026_11_20_without_flag(tmp_path: Path) -> None:
    just_before = tally(tmp_path, CUTOFF.replace(minute=59, hour=14))
    just_before.admit("a", Decimal("1"))
    late = tally(tmp_path, CUTOFF)
    with pytest.raises(CutoffPassed):
        late.admit("b", Decimal("1"))
    assert late.total() == Decimal("1")  # nothing was recorded for the refused run
    late.admit("b", Decimal("1"), after_credit=True)
    late.admit("gh", Decimal("1"), github=True)  # recording a GitHub run is never refused
    assert late.total() == Decimal("3")


def test_crash_keeps_max(tmp_path: Path) -> None:
    path = tmp_path / "spend.jsonl"
    Tally(path).admit("crashed", Decimal("3"))
    assert Tally(path).total() == Decimal("3")  # a new process sees the unsettled maximum


def test_settle_uses_actual(tmp_path: Path) -> None:
    t = tally(tmp_path)
    t.admit("a", Decimal("3"))
    t.settle("a", Decimal("0.75"))
    assert t.total() == Decimal("0.75")


def test_settle_closes_latest_admission_leaving_crashed_one(tmp_path: Path) -> None:
    t = tally(tmp_path)
    t.admit("r", Decimal("3"))  # crashed
    t.admit("r", Decimal("2"))  # resumed under the same id
    t.settle("r", Decimal("1"))
    assert t.total() == Decimal("4")


def test_settle_without_admission_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        tally(tmp_path).settle("ghost", Decimal("1"))


def test_settle_if_complete_only_settles_a_clean_finish(tmp_path: Path) -> None:
    t = tally(tmp_path)
    t.admit("a", Decimal("1"))
    assert not settle_if_complete(t, "a", Decimal("0.1"), completed=False)
    assert t.total() == Decimal("1")
    assert settle_if_complete(t, "a", Decimal("0.1"), completed=True)
    assert t.total() == Decimal("0.1")


def test_records_strings_and_timestamps(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "spend.jsonl"
    Tally(path, clock=lambda: BEFORE).admit("a", Decimal("0.010"))
    line = path.read_text().splitlines()[0]
    assert '"amount": "0.010"' in line and '"run_id": "a"' in line
    assert "2026-11-10T12:00:00+00:00" in line


def test_default_path_is_the_pothole_tally(tmp_path: Path) -> None:
    import ast

    import pothole.budget.tally as module

    # conftest redirects the live constant, so read the real default from the source
    tree = ast.parse(Path(module.__file__).read_text())
    default = next(
        n.value
        for n in tree.body
        if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "DEFAULT_PATH"
    )
    assert ast.unparse(default) == "Path('~/.pothole/spend.jsonl')"
    assert module.DEFAULT_PATH == tmp_path / "spend.jsonl"
    Tally(clock=lambda: BEFORE).admit("a", Decimal("1"))  # no path given: the default is used
    assert (tmp_path / "spend.jsonl").exists()


def test_locked_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "spend.jsonl"
    results: list[bool] = []

    def worker(i: int) -> None:
        try:
            Tally(path, clock=lambda: BEFORE).admit(f"r{i}", Decimal("3"))
            results.append(True)
        except BudgetExceeded:
            results.append(False)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert results.count(True) == 7  # 22 // 3
    assert Tally(path).total() == Decimal("21")


def test_a_test_not_marked_live_gets_the_temporary_tally(tmp_path):
    from pothole.budget import tally

    assert tally.DEFAULT_PATH == tmp_path / "spend.jsonl"


def test_headroom_is_the_cap_less_the_reserve_and_what_is_spent(tmp_path: Path) -> None:
    t = tally(tmp_path)
    t.admit("a", Decimal("2"))
    assert t.headroom() == Decimal("20")  # 25 - 3 kept for GitHub - 2


def test_check_open_follows_the_cutoff(tmp_path: Path) -> None:
    tally(tmp_path).check_open()
    late = tally(tmp_path, CUTOFF)
    with pytest.raises(CutoffPassed):
        late.check_open()
    late.check_open(after_credit=True)
