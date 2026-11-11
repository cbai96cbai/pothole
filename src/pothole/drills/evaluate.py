"""Run a repairer over one split of the drill catalog and keep the record as it goes.

`results/<run-id>/` holds meta.json (written first), then one line per finished case in
outcomes.jsonl and verdicts.jsonl, a trace per case, and summary.json at the end. A case counts as
finished once its verdict line is written; a run started again with the same id skips those cases
and never charges them twice. meta.json says which code made the run (git commit, dirty flag) and
a run can only be continued by the same split, system, deployment and commit.
"""

import json
import statistics
import subprocess
import time
from collections.abc import Callable
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from pothole.budget.limits import DEFAULT_LIMITS, call_max_cost
from pothole.budget.tally import BudgetExceeded, CutoffPassed, Tally
from pothole.dailyai import StepTimeout, with_timeout
from pothole.drills import catalog, scoring
from pothole.drills.catalog import Case
from pothole.drills.scoring import CaseResult, Repairer
from pothole.llm.client import LLMClient
from pothole.repair import agent, routing
from pothole.repair.outcome import RepairOutcome
from pothole.spec.model import PipelineSpec

CASE_TIMEOUT_S = 120.0
CASE_CALLS = 6
CASE_MAX = CASE_CALLS * call_max_cost(DEFAULT_LIMITS)


class RefusedToStart(Exception):
    """The run may not begin: the numbers or the rules say so."""


def needs_repairer(case: Case) -> bool:
    return case.expected_action != "nothing"  # a benign control never calls the repairer


class _Recording:
    """The client, noting each call as it returns so a timed-out case still has a trace."""

    def __init__(self, client: LLMClient, calls: list[dict[str, Any]]) -> None:
        self._client, self._calls = client, calls

    def call(self, messages: Any, *args: Any, **kwargs: Any) -> Any:
        res = self._client.call(messages, *args, **kwargs)
        self._calls.append(
            {"reply": res.message, "finish_reason": res.finish_reason, "cost": str(res.cost)}
        )
        return res


class AgentRepairer:
    """The repair agent as a repairer for one case, seen at the engine's time for its day."""

    def __init__(self, case: Case, client: LLMClient) -> None:
        self.now = catalog.engine_time(case.batch_seen)
        self.partial: list[dict[str, Any]] = []  # calls that returned, kept if the case times out
        self._client = _Recording(client, self.partial)

    def __call__(
        self, spec: PipelineSpec, failed: Any, sample: list[dict[str, Any]], fields: dict[str, str]
    ) -> RepairOutcome:
        strings = [{k: str(v) for k, v in r.items()} for r in sample]
        return agent.repair(
            spec, strings, failed, fields, self._client,  # type: ignore[arg-type]
            now=self.now, history=catalog.history(),
        )  # fmt: skip


def agent_repairer(case: Case, client: LLMClient) -> Repairer:
    return AgentRepairer(case, client)


def git_state(repo: Path = catalog.ROOT) -> tuple[str, bool]:
    """The commit the code is at and whether tracked files have uncommitted changes."""

    def git(*args: str) -> str:
        done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
        if done.returncode:
            raise RefusedToStart(f"cannot read the code version: {done.stderr.strip()}")
        return done.stdout.strip()

    return git("rev-parse", "HEAD"), bool(git("status", "--porcelain", "--untracked-files=no"))


def _lines(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def _append(path: Path, record: dict[str, Any]) -> None:
    with path.open("a") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")


def _refuse_second_test_run(results: Path, meta: dict[str, Any]) -> None:
    if meta["split"] != "test":
        return
    others = [
        m["run_id"]
        for p in results.glob("*/meta.json")
        if (m := json.loads(p.read_text()))["split"] == "test"
        and m["system"] == meta["system"]
        and m["run_id"] != meta["run_id"]
    ]
    if others and not meta["rerun_test"]:
        raise RefusedToStart(
            f"the test split was already run by the {meta['system']} system ({', '.join(others)}); "
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
    case: Case, res: CaseResult, out: RepairOutcome | None, cost: Decimal, seconds: float,
    commit: str,
) -> dict[str, Any]:  # fmt: skip
    return {
        "commit": commit,
        "id": case.id,
        "family": case.family,
        "action": res.action,
        "decided_by": out.decided_by if out else None,
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
) -> tuple[CaseResult, RepairOutcome | None, Decimal, float, dict[str, Any]]:
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
    decide = routing.ruled(inner)  # a volume-only failure is the rule's; no repairer is called

    timed_out: list[bool] = []

    def timed(*args: Any) -> RepairOutcome:
        try:
            box["outcome"] = with_timeout(lambda: decide(*args), timeout_s)
        except StepTimeout:
            timed_out.append(True)
            raise
        return box["outcome"]

    started = time.monotonic()
    res = scoring.run_case(case, spec, timed)
    seconds = time.monotonic() - started
    out = box.get("outcome")
    cost = out.cost if out is not None else (CASE_MAX if charged else Decimal(0))
    if charged and out is not None:
        assert tally is not None
        tally.settle(key, cost)  # a crash or timeout leaves the maximum charged
    trace: dict[str, Any] = {"case": case.id, "trace": out.trace if out else []}
    if out is None:
        trace["trace"] = list(getattr(inner, "partial", []))
        trace["note"] = (
            f"timed out after {timeout_s:g} s; calls that had returned" if timed_out
            else res.reason
        )  # fmt: skip
    return res, out, cost, seconds, trace


def summary_of(
    results: list[CaseResult], rows: list[dict[str, Any]], meta: dict[str, Any], deployment: str
) -> dict[str, Any]:
    """The scoring summary plus cost, time and provenance. Cost per case and the seconds describe
    the cases that called the repairer: a benign control costs nothing and returns at once."""
    body = scoring.summarise(results, meta["split"], meta["system"])
    called_ids = {r.id for r in results if r.expected != "nothing"}
    called = [r for r in rows if r["id"] in called_ids]
    total = sum((Decimal(r["cost"]) for r in rows), Decimal(0))
    secs = [float(r["seconds"]) for r in called]
    commits = sorted({r.get("commit", "unknown") for r in rows})
    body.update(
        run_id=meta["run_id"],
        system=meta["system"],
        rerun_test=meta["rerun_test"],
        total_cost=str(total),
        cases_that_called_the_repairer=len(called),
        cost_per_case=str((total / len(called)).quantize(Decimal("0.00001"))) if called else None,
        mean_seconds_per_case=round(statistics.mean(secs), 2) if secs else None,
        median_seconds_per_case=round(statistics.median(secs), 2) if secs else None,
        averages_over="cases that called the repairer (benign controls excluded)",
        model_deployment=deployment,
        run_date=meta["run_date"],
        code_commit=commits[0] if len(commits) == 1 else "mixed",
        code_commits=commits,
        code_dirty_at_start=meta.get("dirty"),
    )
    return body


def write_summary(out_dir: Path, deployment: str | None = None) -> dict[str, Any]:
    """Rebuild summary.json from the run's own files; no model is called. `complete` says whether
    every case of the split in the catalog has a verdict."""
    meta = json.loads((out_dir / "meta.json").read_text())
    verdicts = _lines(out_dir / "verdicts.jsonl")
    done = {r["id"] for r in verdicts}
    rows = [r for r in _lines(out_dir / "outcomes.jsonl") if r["id"] in done]
    if deployment is None:
        deployment = meta.get("deployment")
    if deployment is None and (out_dir / "summary.json").exists():
        deployment = json.loads((out_dir / "summary.json").read_text()).get("model_deployment")
    body = summary_of([CaseResult(**r) for r in verdicts], rows, meta, deployment or "unknown")
    expected = sum(c.split == meta["split"] for c in catalog.load())
    body.update(complete=len(verdicts) == expected, cases_expected=expected)
    (out_dir / "summary.json").write_text(json.dumps(body, indent=2) + "\n")
    return body


def _check_same_run(old: dict[str, Any], new: dict[str, Any], dirty: bool) -> None:
    """A run continues only on the same clean commit: the tree was clean when it started and is
    clean now, so the cases already scored were made by the code that finishes the run."""
    differs = [
        f"{k}: run has {old.get(k, 'not recorded')!r}, now {new[k]!r}"
        for k in ("split", "system", "deployment", "commit")
        if old.get(k) != new[k]
    ]
    if differs:
        raise RefusedToStart("run id already holds a different run; " + "; ".join(differs))
    if old.get("dirty") is not False or dirty:
        raise RefusedToStart(
            "a run is continued only from a clean tree at the same commit; it had uncommitted "
            f"changes at start: {old.get('dirty', 'not recorded')!r}, now: {dirty}"
        )


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
    code: tuple[str, bool] | None = None,
) -> dict[str, Any]:
    spec = catalog.load_spec()
    todo = cases if cases is not None else [c for c in catalog.load() if c.split == split]
    commit, dirty = code or git_state()
    out_dir = results / run_id
    meta = {
        "run_id": run_id,
        "split": split,
        "system": system,
        "deployment": deployment,
        "commit": commit,
        "dirty": dirty,
        "rerun_test": rerun_test,
        "run_date": today.isoformat(),
    }
    if (out_dir / "meta.json").exists():  # a resume: the run's record stays as it was first written
        meta = json.loads((out_dir / "meta.json").read_text())
        same = {"split": split, "system": system, "deployment": deployment, "commit": commit}
        _check_same_run(meta, same, dirty)
    _refuse_second_test_run(results, meta)
    done = {r["id"] for r in _lines(out_dir / "verdicts.jsonl")}
    remaining = [c for c in todo if c.id not in done]
    _preflight(tally, sum(needs_repairer(c) for c in remaining), after_credit)
    (out_dir / "traces").mkdir(parents=True, exist_ok=True)
    if not (out_dir / "meta.json").exists():
        (out_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    # drop a line left by a case that stopped before its verdict was written
    rows = [r for r in _lines(out_dir / "outcomes.jsonl") if r["id"] in done]
    (out_dir / "outcomes.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    for case in remaining:
        res, out, cost, seconds, trace = _one(case, spec, make, tally, run_id, timeout_s)
        (out_dir / "traces" / f"{case.id}.json").write_text(
            json.dumps(trace, indent=2, default=str) + "\n"
        )
        _append(out_dir / "outcomes.jsonl", _outcome_line(case, res, out, cost, seconds, commit))
        _append(out_dir / "verdicts.jsonl", asdict(res))  # last: it marks the case finished
    return write_summary(out_dir, deployment)
