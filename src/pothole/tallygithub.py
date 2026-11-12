"""Admit the GitHub daily runs that may have used the model into the local build tally.

Every attempt of a scheduled daily run on or after `since` is charged the run envelope once,
whatever its conclusion (success, failure, cancelled, timed out), unless that attempt's committed
record says AI was off. A run with no committed record, as after a crash, is charged, unless
GitHub says the attempt's `run` job was skipped (the DAILY_ENABLED gate): nothing ran. A re-run
keeps its run id and raises the attempt number, so each attempt is its own charge (idempotent by
run id and attempt). Manual dispatches never use the model and are not listed here.

The tally never under-counts silently: the listing is asked for with a limit, and while it comes
back full the limit is doubled and it is asked for again, back to `since`; if it is still full at
MAX_RUNS the tally stops with an error rather than count a list that may be cut short.
"""

import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from pothole import runrecord
from pothole.budget.envelope import run_envelope
from pothole.budget.tally import Tally

Runner = Callable[[list[str]], str]
SCHEDULE = "schedule"
FIRST_LIMIT = 200
MAX_RUNS = 6400


class RunListTruncated(RuntimeError):
    """`gh run list` still came back full at the largest limit: older runs may be missing."""


def gh_runner(args: list[str]) -> str:
    done = subprocess.run(args, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise RuntimeError(f"{' '.join(args[:3])} failed: {(done.stderr or done.stdout)[-500:]}")
    return done.stdout


@dataclass
class Admitted:
    admitted: list[str] = field(default_factory=list)  # "<run id>-<attempt>"
    already: list[str] = field(default_factory=list)
    ai_off: list[str] = field(default_factory=list)  # the attempt's record says AI was off
    without_record: list[str] = field(default_factory=list)  # charged, no committed record
    gated_off: list[str] = field(default_factory=list)  # the run job was skipped: nothing spent


def _listing(repo: str, since: date, run: Runner) -> list[dict[str, Any]]:
    """Every run of the daily workflow created on or after `since`, however many there are."""
    limit = FIRST_LIMIT
    while True:
        out = run(
            [
                "gh", "run", "list", "--repo", repo, "--workflow", "daily.yml",
                "--created", f">={since.isoformat()}", "--limit", str(limit),
                "--json", "databaseId,conclusion,status,createdAt,event,attempt",
            ]
        )  # fmt: skip
        listed: list[dict[str, Any]] = json.loads(out)
        if len(listed) < limit:
            return listed
        if limit >= MAX_RUNS:
            raise RunListTruncated(
                f"gh run list returned {len(listed)} runs at its largest limit: "
                "older runs may be missing, so the tally was not changed"
            )
        limit = min(limit * 2, MAX_RUNS)


def _run_job_skipped(repo: str, run_id: object, attempt: int, run: Runner) -> bool:
    """True only when GitHub reports this attempt's `run` job as skipped. Anything else, an
    unreadable answer included, is not proof that nothing ran, so the attempt is charged."""
    out = run(
        [
            "gh", "run", "view", str(run_id), "--repo", repo, "--attempt", str(attempt),
            "--json", "jobs",
        ]
    )  # fmt: skip
    try:
        jobs = json.loads(out).get("jobs", [])
    except (json.JSONDecodeError, AttributeError):
        return False
    return any(j.get("name") == "run" and j.get("conclusion") == "skipped" for j in jobs)


def tally_github(
    repo: str,
    since: date,
    records: Path,
    tally: Tally,
    run: Runner = gh_runner,
) -> Admitted:
    listed = _listing(repo, since, run)
    seen: dict[tuple[str, int], list[runrecord.RunRecord]] = {}
    for r in runrecord.read(records):
        seen.setdefault((r.run_id, r.attempt), []).append(r)
    amount = run_envelope()
    result = Admitted()
    for item in sorted(listed, key=lambda i: i["databaseId"]):
        if item["event"] != SCHEDULE or item["status"] != "completed":
            continue
        if date.fromisoformat(item["createdAt"][:10]) < since:
            continue
        for attempt in range(1, int(item.get("attempt") or 1) + 1):
            tag = f"{item['databaseId']}-{attempt}"
            held = seen.get((str(item["databaseId"]), attempt), [])
            if held and not any(r.ai_enabled for r in held):
                result.ai_off.append(tag)
            elif tally.has(f"github-{tag}"):
                result.already.append(tag)
            elif not held and _run_job_skipped(repo, item["databaseId"], attempt, run):
                result.gated_off.append(tag)
            else:
                tally.admit(f"github-{tag}", amount, github=True)
                result.admitted.append(tag)
                if not held:
                    result.without_record.append(tag)
    return result
