"""Admit the GitHub daily runs that may have used the model into the local build tally.

Every attempt of a scheduled daily run on or after `since` is charged the run envelope once,
whatever its conclusion (success, failure, cancelled, timed out), unless that attempt's committed
record says AI was off. A run with no committed record, as after a crash, is charged. A re-run
keeps its run id and raises the attempt number, so each attempt is its own charge (idempotent by
run id and attempt). Manual dispatches never use the model and are not listed here.
"""

import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from pothole import runrecord
from pothole.budget.envelope import run_envelope
from pothole.budget.tally import Tally

Runner = Callable[[list[str]], str]
SCHEDULE = "schedule"


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


def tally_github(
    repo: str,
    since: date,
    records: Path,
    tally: Tally,
    run: Runner = gh_runner,
) -> Admitted:
    out = run(
        [
            "gh",
            "run",
            "list",
            "--repo",
            repo,
            "--workflow",
            "daily.yml",
            "--limit",
            "200",
            "--json",
            "databaseId,conclusion,status,createdAt,event,attempt",
        ]  # fmt: skip
    )
    seen: dict[tuple[str, int], list[runrecord.RunRecord]] = {}
    for r in runrecord.read(records):
        seen.setdefault((r.run_id, r.attempt), []).append(r)
    amount = run_envelope()
    result = Admitted()
    for item in sorted(json.loads(out), key=lambda i: i["databaseId"]):
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
            else:
                tally.admit(f"github-{tag}", amount, github=True)
                result.admitted.append(tag)
                if not held:
                    result.without_record.append(tag)
    return result
