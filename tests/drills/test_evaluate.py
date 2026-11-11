import json
import threading
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from pothole.budget.tally import CUTOFF, Tally
from pothole.drills import baseline, evaluate
from pothole.drills.catalog import Case
from pothole.drills.evaluate import CASE_MAX, RefusedToStart, run_split
from pothole.llm.client import CallResult, Usage
from pothole.repair.outcome import RepairOutcome

TODAY = date(2026, 11, 11)
DAY = datetime(2026, 11, 11, 12, tzinfo=UTC)
IDS = ["renamed_field-0", "field_dropped-0", "benign_field-0"]  # patch, issue, benign control


@pytest.fixture
def three(cases: dict[str, Case]) -> list[Case]:
    return [cases[i] for i in IDS]


def tally(tmp_path: Path, cap: str = "25", now: datetime = DAY) -> Tally:
    return Tally(tmp_path / "spend.jsonl", Decimal(cap), clock=lambda: now)


def issuer(cost: str = "0.01", seen: list[str] | None = None) -> Any:
    def make(case: Case) -> Any:
        def repairer(*_: Any) -> RepairOutcome:
            if seen is not None:
                seen.append(case.id)
            return RepairOutcome("issue", reason="asks a person", cost=Decimal(cost))

        return repairer

    return make


def go(tmp_path: Path, make: Any, cases: list[Case], t: Tally | None, **kw: Any) -> dict[str, Any]:
    kw.setdefault("run_id", "r1")
    kw.setdefault("split", "dev")
    return run_split(
        kw.pop("split"), kw.pop("system", "agent"), kw.pop("run_id"), tmp_path / "results", make,
        tally=t, deployment="dep", today=TODAY, cases=cases, **kw,
    )  # fmt: skip


def spend(t: Tally) -> list[dict[str, Any]]:
    return [json.loads(x) for x in t.path.read_text().splitlines()]


def test_each_case_is_admitted_at_its_maximum_and_settled_at_its_cost(tmp_path, three):
    t = tally(tmp_path)
    summary = go(tmp_path, issuer(), three, t)
    events = [(e["event"], e["amount"]) for e in spend(t)]
    # two cases call the repairer; the benign control is never charged
    assert events == [("admit", str(CASE_MAX)), ("settle", "0.01")] * 2
    assert CASE_MAX == Decimal("0.048")
    assert t.total() == Decimal("0.02")
    assert summary["total_cost"] == "0.02" and summary["model_deployment"] == "dep"
    assert summary["run_date"] == "2026-11-11" and summary["cases"] == 3


def test_the_run_writes_every_file(tmp_path, three):
    go(tmp_path, issuer(), three, tally(tmp_path))
    out = tmp_path / "results" / "r1"
    rows = [json.loads(x) for x in (out / "outcomes.jsonl").read_text().splitlines()]
    assert [r["id"] for r in rows] == IDS
    assert set(rows[0]) == {"id", "family", "action", "ops", "reason", "cost", "seconds"}
    assert len((out / "verdicts.jsonl").read_text().splitlines()) == 3
    assert {p.name for p in (out / "traces").iterdir()} == {f"{i}.json" for i in IDS}
    assert json.loads((out / "summary.json").read_text())["mean_seconds_per_case"] is not None


def test_a_split_that_does_not_fit_is_refused_before_anything_runs(tmp_path, three):
    t = tally(tmp_path, cap="3.05")  # 0.05 to spare, two repairer cases need 0.096
    seen: list[str] = []
    with pytest.raises(RefusedToStart, match=r"2 cases x 0.048 = 0.096.*0.05"):
        go(tmp_path, issuer(seen=seen), three, t)
    assert seen == [] and spend(t) == []
    assert not (tmp_path / "results" / "r1").exists()


def test_after_the_cutoff_needs_after_credit(tmp_path, three):
    late = tally(tmp_path, now=CUTOFF)
    with pytest.raises(RefusedToStart, match="after_credit"):
        go(tmp_path, issuer(), three, late)
    assert go(tmp_path, issuer(), three, late, after_credit=True)["cases"] == 3


def test_a_hanging_repairer_is_a_crashed_case_charged_its_maximum(tmp_path, three):
    release = threading.Event()

    def make(case: Case) -> Any:
        def hang(*_: Any) -> RepairOutcome:
            release.wait(5)
            return RepairOutcome("issue")

        return hang

    t = tally(tmp_path)
    try:
        summary = go(tmp_path, make, three[:2], t, timeout_s=0.1)
    finally:
        release.set()
    assert summary["crashed"] == 2 and summary["repair_success"]["correct"] == 0
    assert t.total() == 2 * CASE_MAX  # never settled
    row = json.loads((tmp_path / "results" / "r1" / "outcomes.jsonl").read_text().splitlines()[0])
    assert row["action"] == "crashed" and row["cost"] == str(CASE_MAX)
    assert "no answer" in row["reason"]


def test_a_run_started_again_skips_finished_cases_and_never_recharges_them(tmp_path, three):
    t = tally(tmp_path)
    go(tmp_path, issuer(), three[:1], t)
    seen: list[str] = []
    summary = go(tmp_path, issuer(seen=seen), three, t)
    assert seen == ["field_dropped-0"]  # the first case was not run again
    assert t.total() == Decimal("0.02") and len(spend(t)) == 4
    assert summary["cases"] == 3 and summary["total_cost"] == "0.02"
    lines = (tmp_path / "results" / "r1" / "outcomes.jsonl").read_text().splitlines()
    assert [json.loads(x)["id"] for x in lines] == IDS


def test_a_case_stopped_before_its_verdict_is_run_again_not_skipped(tmp_path, three):
    go(tmp_path, issuer(), three[:1], None)
    out = tmp_path / "results" / "r1"
    (out / "verdicts.jsonl").write_text("")  # the stop came after the outcome line
    seen: list[str] = []
    go(tmp_path, issuer(seen=seen), three[:1], None)
    assert seen == ["renamed_field-0"]
    assert len((out / "outcomes.jsonl").read_text().splitlines()) == 1


def test_the_test_split_runs_once_per_system(tmp_path, cases):
    one = [cases["comma_thousands-0"]]
    go(tmp_path, issuer(), one, None, split="test", run_id="t1")
    go(tmp_path, issuer(), one, None, split="test", run_id="t1")  # the same run resumes
    with pytest.raises(RefusedToStart, match="--rerun-test"):
        go(tmp_path, issuer(), one, None, split="test", run_id="t2")
    again = go(tmp_path, issuer(), one, None, split="test", run_id="t2", rerun_test=True)
    assert again["rerun_test"] is True
    # the baseline and the dev split are not limited
    go(tmp_path, issuer(), one, None, split="test", system="baseline", run_id="b1")
    go(tmp_path, issuer(), one, None, split="dev", run_id="d1")
    go(tmp_path, issuer(), one, None, split="dev", run_id="d2")


def test_the_baseline_runs_free_and_scores_like_the_stored_summary(tmp_path, three):
    summary = go(tmp_path, lambda case: baseline.repair, three, None, system="baseline")
    assert summary["total_cost"] == "0" and summary["system"] == "baseline"
    assert summary["per_family"]["benign_field"]["correct"] == 1


class Model:
    def __init__(self) -> None:
        self.sent: list[Any] = []

    def call(self, messages: Any, tools: Any = None, limits: Any = None, tool_choice: Any = None):
        self.sent.append([dict(m) for m in messages])
        submit = {
            "id": "c1",
            "type": "function",
            "function": {
                "name": "submit",
                "arguments": json.dumps({"action": "issue", "reason": "needs a person"}),
            },
        }
        message = {"role": "assistant", "content": None, "tool_calls": [submit]}
        return CallResult(message, Usage(1000, 100, Decimal("0.001")), "tool_calls", 0.0)


def test_the_agent_adapter_gives_the_agent_the_case_sample_and_reports_its_cost(tmp_path, three):
    model = Model()
    t = tally(tmp_path)
    summary = go(tmp_path, lambda c: evaluate.agent_repairer(c, model), three[1:2], t)  # type: ignore[arg-type]
    assert len(model.sent) == 1 and "Failed checks" in model.sent[0][1]["content"]
    assert summary["issue_accuracy"]["correct"] == 1 and summary["total_cost"] == "0.001"
    assert t.total() == Decimal("0.001")
    trace = json.loads(
        (tmp_path / "results" / "r1" / "traces" / "field_dropped-0.json").read_text()
    )
    assert len(trace["trace"]) == 1
