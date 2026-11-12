"""A tiny synthetic repo of committed files: 3 run records, 1 incident, 1 drill, 1 quiet day,
1 day with a finding (a series and 20 points), the measurement files and the real baselines."""

import json
import shutil
from datetime import date
from pathlib import Path

from pothole import runrecord
from pothole.analyst.finding import AnalystDay, Evidence, Finding, Weather, to_json
from pothole.repair.render import SPEC_PATH
from pothole.runrecord import CheckResult, RepairRecord, RunRecord

REAL = Path(__file__).resolve().parents[2]
OK = CheckResult("row_count", "pass", "48,210 rows")
BAD = CheckResult("borough_values", "fail", "borough had 9 new values")


def _run(n: int, day: int, **kw: object) -> RunRecord:
    base: dict[str, object] = dict(
        run_id=f"run-{n}",
        started=f"2026-11-{day:02d}T07:00:00+00:00",
        finished=f"2026-11-{day:02d}T07:05:00+00:00",
        status="success",
        rows_loaded=48210,
        pages=1,
        checks=[OK],
        analyst_day=f"2026-11-{day - 2:02d}",
        ai_cost="0.021",
    )
    return RunRecord(**{**base, **kw})  # type: ignore[arg-type]


def finding(status: str = "investigated", stale: bool = False) -> Finding:
    """`unfinished` has the real shape: an empty summary. `stale` is an unfinished finding that
    still holds a summary, which the page must not show."""
    unfinished = status == "unfinished"
    return Finding(
        id="2026-11-05-bronx-2-rodent"
        if stale
        else ("2026-11-06-queens-6-noise" if unfinished else "2026-11-06-brooklyn-12-heat"),
        day=date(2026, 11, 6),
        borough="Brooklyn",
        board=12,
        complaint_type="HEAT/HOT WATER",
        observed=31,
        expected=6.5,
        ratio=4.77,
        q=0.0004,
        summary=("a leftover summary that must not be shown" if stale else "")
        if unfinished
        else "Heat complaints in Brooklyn 12 (\"No heat\" in 20) were associated with a night's cold.",
        evidence=[]
        if unfinished
        else [
            Evidence(1, "breakdown", {"by": "descriptor", "top": 3}, "31 on 2026-11-06"),
            Evidence(
                2,
                "weather",
                {},
                '{"precip_mm": 0.0, "tmax_c": 3.1, "source": "forecast_past_days"}',
            ),
            Evidence(
                4,
                "events",
                {},
                '{"scope": "s", "truncated": false, "events": [{"name": "Street fair", '
                '"type": "Street Event", "start": "2026-11-06T10:00:00.000", '
                '"end": "2026-11-06T18:00:00.000"}], "shown": "first 1 of 2"}',
            ),
        ],
        series=[(date(2026, 10, 10 + i), 5 + i % 3) for i in range(20)] + [(date(2026, 11, 6), 31)],
        points=[(40.63 + i * 0.001, -73.99 + i * 0.001) for i in range(20)],
        weather=Weather(0.0, 3.1, "archive"),
        events=[],
        status=status,  # type: ignore[arg-type]
        trace=[{"call": 1, "tool_results": ["31"]}],
        notes=["unfinished: the model gave no accepted answer", "events: no rows"]
        if unfinished
        else [],
        data_starts=None if unfinished else date(2026, 10, 10),
        events_state="unavailable" if unfinished else "complete",
    )


def make(root: Path, *, with_agent: bool = False) -> Path:
    runs = root / "data" / "runs.jsonl"
    runrecord.append(runs, _run(1, 8))
    runrecord.append(
        runs,
        _run(
            2,
            9,
            status="checks_failed",
            checks=[BAD],
            repair=RepairRecord("patch", False, "https://github.com/x/y/pull/3", None, True),
        ),
    )
    runrecord.append(
        runs,
        _run(
            3,
            10,
            repair=RepairRecord("patch", True, "https://github.com/x/y/pull/4", "correct", False),
        ),
    )
    analyst = root / "data" / "analyst"
    analyst.mkdir(parents=True)
    quiet = AnalystDay(date(2026, 11, 7), 1452, 0, [])
    (analyst / "2026-11-07.json").write_text(json.dumps(to_json(quiet)))
    busy = AnalystDay(date(2026, 11, 6), 1460, 3, [finding(), finding("unfinished")])
    (analyst / "2026-11-06.json").write_text(json.dumps(to_json(busy)))
    stale = AnalystDay(date(2026, 11, 5), 1440, 1, [finding("unfinished", stale=True)])
    (analyst / "2026-11-05.json").write_text(json.dumps(to_json(stale)))
    scratch_repo(root)
    if with_agent:
        copy_runs(root, AGENT_RUNS)
        shutil.copy(REAL / "results" / COMPARE, root / "results")
        publish(root, [*BASELINE_RUNS, *AGENT_RUNS], compare=COMPARE)
    return root


def scratch_repo(root: Path) -> Path:
    """The files a run reads from a checkout: the pinned spec at the spec path, the universe, the
    measurement files and the baselines."""
    (root / SPEC_PATH).parent.mkdir(parents=True, exist_ok=True)
    (root / "data").mkdir(exist_ok=True)
    shutil.copy(REAL / "fixtures" / "batches" / "spec.yaml", root / SPEC_PATH)  # pinned copy
    shutil.copy(REAL / "data" / "universe.json", root / "data")
    for name in ("backtest", "delay", "null_simulation"):
        shutil.copy(REAL / "data" / f"{name}.json", root / "data")
    copy_runs(root, BASELINE_RUNS)
    publish(root, BASELINE_RUNS)
    return root


# the frozen runs the real repo publishes: the fixture copies finished, clean, one-commit records
BASELINE_RUNS = ["baseline-final-dev", "baseline-final-test"]
AGENT_RUNS = ["agent-final-dev-a", "agent-final-dev-b", "agent-final-test"]
COMPARE = "compare-agent-final-test-baseline-final-test.json"


def copy_runs(root: Path, runs: list[str]) -> None:
    for run in runs:
        dest = root / "results" / run
        dest.mkdir(parents=True)
        for name in ("summary.json", "meta.json"):
            shutil.copy(REAL / "results" / run / name, dest)


def publish(root: Path, runs: list[str], compare: str | None = None) -> None:
    """Write the manifest naming the runs the site publishes, in the given order."""
    manifest: dict[str, object] = {
        "dev": [r for r in runs if "-dev" in r],
        "test": [r for r in runs if "-test" in r],
    }
    if compare:
        manifest["compare"] = compare
    (root / "results" / "published.json").write_text(json.dumps(manifest))
