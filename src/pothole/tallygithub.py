"""Admit the GitHub runs that had AI enabled into the local build tally.

Every completed or failed daily run whose committed record says AI was enabled is charged the
run envelope once (idempotent by run id), so the tally stays conservative however the run ended.
"""

import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from pothole import runrecord
from pothole.budget.envelope import run_envelope
from pothole.budget.tally import Tally

Runner = Callable[[list[str]], str]
DONE = ("success", "failure")


def gh_runner(args: list[str]) -> str:
    done = subprocess.run(args, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise RuntimeError(f"{' '.join(args[:3])} failed: {(done.stderr or done.stdout)[-500:]}")
    return done.stdout


@dataclass(frozen=True)
class Admitted:
    admitted: list[str]
    already: list[str]
    no_record: list[str]  # finished runs whose record was never committed: not charged


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
            "databaseId,conclusion,status,createdAt",
        ]  # fmt: skip
    )
    listed = json.loads(out)
    known = {r.run_id: r for r in runrecord.read(records)}
    amount = run_envelope()
    result = Admitted([], [], [])
    for item in sorted(listed, key=lambda i: i["databaseId"]):
        run_id = str(item["databaseId"])
        if item["status"] != "completed" or item["conclusion"] not in DONE:
            continue
        if date.fromisoformat(item["createdAt"][:10]) < since:
            continue
        record = known.get(run_id)
        if record is None:
            result.no_record.append(run_id)
        elif not record.ai_enabled:
            continue
        elif tally.has(f"github-{run_id}"):
            result.already.append(run_id)
        else:
            tally.admit(f"github-{run_id}", amount, github=True)
            result.admitted.append(run_id)
    return result
