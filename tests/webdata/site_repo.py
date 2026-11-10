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
OK = CheckResult("row_count", "ok", "48,210 rows")
BAD = CheckResult("borough_values", "error", "borough had 9 new values")


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


def finding(status: str = "investigated") -> Finding:
    unfinished = status == "unfinished"
    return Finding(
        id="2026-11-06-queens-6-noise" if unfinished else "2026-11-06-brooklyn-12-heat",
        day=date(2026, 11, 6),
        borough="Brooklyn",
        board=12,
        complaint_type="HEAT/HOT WATER",
        observed=31,
        expected=6.5,
        ratio=4.77,
        q=0.0004,
        summary=""
        if unfinished
        else "Heat complaints in Brooklyn 12 were associated with a cold night.",
        evidence=[]
        if unfinished
        else [Evidence("daily_counts", {"board": 12}, "31 on 2026-11-06")],
        series=[(date(2026, 10, 10 + i), 5 + i % 3) for i in range(20)] + [(date(2026, 11, 6), 31)],
        points=[(40.63 + i * 0.001, -73.99 + i * 0.001) for i in range(20)],
        weather=Weather(0.0, 3.1, "archive"),
        events=[],
        status=status,  # type: ignore[arg-type]
        trace=[{"call": 1, "tool_results": ["31"]}],
        notes=["unfinished: the model gave no accepted answer", "events: no rows"]
        if unfinished
        else [],
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
    (root / SPEC_PATH).parent.mkdir()
    shutil.copy(REAL / SPEC_PATH, root / SPEC_PATH)
    shutil.copy(REAL / "data" / "universe.json", root / "data")
    for name in ("backtest", "delay", "null_simulation"):
        shutil.copy(REAL / "data" / f"{name}.json", root / "data")
    for split in ("dev", "test"):
        dest = root / "results" / f"baseline-{split}"
        dest.mkdir(parents=True)
        shutil.copy(REAL / "results" / f"baseline-{split}" / "summary.json", dest)
    if with_agent:
        src = json.loads((REAL / "results/baseline-test/summary.json").read_text())
        dest = root / "results" / "agent-test"
        dest.mkdir(parents=True)
        (dest / "summary.json").write_text(json.dumps({**src, "repairer": "agent"}))
    return root
