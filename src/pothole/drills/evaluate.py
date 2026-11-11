"""Run a repairer over one split of the drill catalog and keep the record as it goes.

`results/<run-id>/` holds meta.json (written first), then one line per finished case in
outcomes.jsonl and verdicts.jsonl, a trace per case, and summary.json at the end. A case counts as
finished once its verdict line is written; a run started again with the same id skips those cases
and never charges them twice.
"""

import json
import statistics
import time
from collections.abc import Callable
from dataclasses import asdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from pothole.budget.limits import DEFAULT_LIMITS, call_max_cost
from pothole.budget.tally import BudgetExceeded, CutoffPassed, Tally
from pothole.dailyai import with_timeout
from pothole.drills import catalog, scoring
from pothole.drills.catalog import Case
from pothole.drills.scoring import CaseResult, Repairer
from pothole.llm.client import LLMClient
from pothole.repair import agent
from pothole.repair.outcome import RepairOutcome
from pothole.spec.model import PipelineSpec

CASE_TIMEOUT_S = 120.0
CASE_CALLS = 6
CASE_MAX = CASE_CALLS * call_max_cost(DEFAULT_LIMITS)


class RefusedToStart(Exception):
    """The run may not begin: the numbers or the rules say so."""


def needs_repairer(case: Case) -> bool:
    return case.expected_action != "nothing"  # a benign control never calls the repairer


def agent_repairer(case: Case, client: LLMClient) -> Repairer:
    """The repair agent as a repairer for one case, given the day the case was seen."""
    now = datetime.strptime(case.batch_seen, "%Y-%m-%d") + timedelta(days=1, hours=30)

    def run(
        spec: PipelineSpec, failed: Any, sample: list[dict[str, Any]], fields: dict[str, str]
    ) -> RepairOutcome:
        strings = [{k: str(v) for k, v in r.items()} for r in sample]
        return agent.repair(
            spec, strings, failed, fields, client, now=now, history=catalog.history()
        )

    return run


def _lines(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def _append(path: Path, record: dict[str, Any]) -> None:
    with path.open("a") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")


def _refuse_second_test_run(results: Path, meta: dict[str, Any]) -> None:
    if meta["split"] != "test" or meta["system"] != "agent":
        return
    others = [
        m["run_id"]
        for p in results.glob("*/meta.json")
        if (m := json.loads(p.read_text()))["split"] == "test"
        and m["system"] == "agent"
        and m["run_id"] != meta["run_id"]
    ]
    if others and not meta["rerun_test"]:
        raise RefusedToStart(
            f"the test split was already run by the agent ({', '.join(others)}); "
            "a second run needs a different --run-id and --rerun-test"
        )


def _preflight(tally: Tally | None, remaining: int, after_credit: bool) -> None:
    if tally is None:
        return
    try:
        tally.check_open(after_credit=after_credit)
    except CutoffPassed as err:
        raise RefusedToStart(str(err)) from err
    need, room = remaining * CASE_MAX, tally.headroom()
    if need > room:
        raise RefusedToStart(
            f"{remaining} cases x {CASE_MAX} = {need} maximum, but the tally has {room} to spare"
        )


def _outcome_line(
    case: Case, res: CaseResult, out: RepairOutcome | None, cost: Decimal, seconds: float
) -> dict[str, Any]:
    return {
        "id": case.id,
        "family": case.family,
        "action": res.action,
        "ops": [o.model_dump() for o in out.ops] if out else [],
        "reason": out.reason if out and res.action != "crashed" else res.reason,
        "cost": str(cost),
        "seconds": round(seconds, 2),
    }


def _one(
    case: Case,
    spec: PipelineSpec,
    make: Callable[[Case], Repairer],
    tally: Tally | None,
    run_id: str,
    timeout_s: float,
) -> tuple[CaseResult, RepairOutcome | None, Decimal, float]:
    """One case: reserve its maximum, run it under a timeout, settle what it really cost."""
    charged = needs_repairer(case) and tally is not None
    key = f"eval:{run_id}:{case.id}"
    if charged:
        assert tally is not None
        try:
            tally.admit(key, CASE_MAX, after_credit=True)  # the cutoff was checked at the start
        except BudgetExceeded as err:
            raise RefusedToStart(str(err)) from err
    box: dict[str, RepairOutcome] = {}
    inner = make(case)

    def timed(*args: Any) -> RepairOutcome:
        box["outcome"] = with_timeout(lambda: inner(*args), timeout_s)
        return box["outcome"]

    started = time.monotonic()
    res = scoring.run_case(case, spec, timed)
    seconds = time.monotonic() - started
    out = box.get("outcome")
    cost = out.cost if out is not None else (CASE_MAX if charged else Decimal(0))
    if charged and out is not None:
        assert tally is not None
        tally.settle(key, cost)  # a crash or timeout leaves the maximum charged
    return res, out, cost, seconds


def summary_of(
    results: list[CaseResult], rows: list[dict[str, Any]], meta: dict[str, Any], deployment: str
) -> dict[str, Any]:
    body = scoring.summarise(results, meta["split"], meta["system"])
    costs = [Decimal(r["cost"]) for r in rows]
    secs = [float(r["seconds"]) for r in rows]
    total = sum(costs, Decimal(0))
    body.update(
        run_id=meta["run_id"],
        system=meta["system"],
        rerun_test=meta["rerun_test"],
        total_cost=str(total),
        cost_per_case=str((total / len(rows)).quantize(Decimal("0.00001"))) if rows else None,
        mean_seconds_per_case=round(statistics.mean(secs), 2) if secs else None,
        median_seconds_per_case=round(statistics.median(secs), 2) if secs else None,
        model_deployment=deployment,
        run_date=meta["run_date"],
    )
    return body


def run_split(
    split: str,
    system: str,
    run_id: str,
    results: Path,
    make: Callable[[Case], Repairer],
    *,
    tally: Tally | None,
    deployment: str,
    today: date,
    timeout_s: float = CASE_TIMEOUT_S,
    after_credit: bool = False,
    rerun_test: bool = False,
    cases: list[Case] | None = None,
) -> dict[str, Any]:
    spec = catalog.load_spec()
    todo = cases if cases is not None else [c for c in catalog.load() if c.split == split]
    out_dir = results / run_id
    meta = {
        "run_id": run_id,
        "split": split,
        "system": system,
        "rerun_test": rerun_test,
        "run_date": today.isoformat(),
    }
    _refuse_second_test_run(results, meta)
    done = {r["id"] for r in _lines(out_dir / "verdicts.jsonl")}
    remaining = [c for c in todo if c.id not in done]
    _preflight(tally, sum(needs_repairer(c) for c in remaining), after_credit)
    (out_dir / "traces").mkdir(parents=True, exist_ok=True)
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    # drop a line left by a case that stopped before its verdict was written
    rows = [r for r in _lines(out_dir / "outcomes.jsonl") if r["id"] in done]
    (out_dir / "outcomes.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    for case in remaining:
        res, out, cost, seconds = _one(case, spec, make, tally, run_id, timeout_s)
        line = _outcome_line(case, res, out, cost, seconds)
        trace = out.trace if out else []
        (out_dir / "traces" / f"{case.id}.json").write_text(
            json.dumps({"case": case.id, "trace": trace}, indent=2, default=str) + "\n"
        )
        _append(out_dir / "outcomes.jsonl", line)
        _append(out_dir / "verdicts.jsonl", asdict(res))  # last: it marks the case finished
        rows.append(line)
    verdicts = {r["id"]: CaseResult(**r) for r in _lines(out_dir / "verdicts.jsonl")}
    results_in_order = [verdicts[c.id] for c in todo if c.id in verdicts]
    body = summary_of(results_in_order, rows, meta, deployment)
    (out_dir / "summary.json").write_text(json.dumps(body, indent=2) + "\n")
    return body
