"""The rules that stop a run from starting: an earlier test run, or a different run under the
same id."""

import json
from pathlib import Path
from typing import Any


class RefusedToStart(Exception):
    """The run may not begin: the numbers or the rules say so."""


def _earlier_test_runs(results: Path, system: str, run_id: str) -> list[str]:
    """Every other run folder that scored the test split for this system. A folder with a meta
    says so there; one without (the legacy baseline) says so in its summary."""
    found = []
    for folder in sorted(p for p in results.iterdir() if p.is_dir()) if results.exists() else []:
        meta, summary = folder / "meta.json", folder / "summary.json"
        if meta.exists():
            record = json.loads(meta.read_text())
            split, who = record["split"], record["system"]
        elif summary.exists():
            record = json.loads(summary.read_text())
            split, who = record.get("split"), record.get("system") or record.get("repairer")
        else:
            continue
        if split == "test" and who == system and folder.name != run_id:
            found.append(folder.name)
    return found


def refuse_second_test_run(results: Path, meta: dict[str, Any]) -> None:
    """Guards the start of a new run; continuing a recorded run is never refused here."""
    if meta["split"] != "test":
        return
    others = _earlier_test_runs(results, meta["system"], meta["run_id"])
    if others and not meta["rerun_test"]:
        raise RefusedToStart(
            f"the test split was already run by the {meta['system']} system ({', '.join(others)}); "
            "a second run needs a different --run-id and --rerun-test"
        )


def check_same_run(old: dict[str, Any], new: dict[str, Any], dirty: bool) -> None:
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
