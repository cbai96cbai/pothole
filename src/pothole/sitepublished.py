"""The repair scores the site publishes: only the runs `results/published.json` names, each a
finished, clean, frozen run whose records agree, with every score split by who decided the case.
Other folders under `results/` stay there as records and are never shown."""

import json
import re
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from pothole import sitemodels as m

MANIFEST = "published.json"
RUN_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
SPLITS = ("dev", "test")
RATES = ("repair_success", "issue_accuracy", "no_change_accuracy")


def _read(path: Path, what: str) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"{what}: {path.name} is missing")
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{what}: {path.name} is not an object")
    return data


def _listed(manifest: dict[str, Any]) -> list[tuple[str, str]]:
    """(split, run id) in the order the manifest lists them."""
    out: list[tuple[str, str]] = []
    for split in SPLITS:
        ids = manifest.get(split)
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            raise ValueError(f"{MANIFEST}: {split!r} must be a list of run ids")
        for run in ids:
            if not RUN_NAME.match(run):
                raise ValueError(f"{MANIFEST}: {run!r} is not a run folder name")
            if run in [r for _, r in out]:
                raise ValueError(f"{MANIFEST}: run {run} is listed twice")
            out.append((split, run))
    return out


def _check_run(run: str, split: str, meta: dict[str, Any], summary: dict[str, Any]) -> None:
    def bad(why: str) -> ValueError:
        return ValueError(f"published run {run}: {why}")

    if summary.get("complete") is not True:
        raise bad("the run is not complete")
    if meta.get("dirty") is not False:
        raise bad("the run was made from a changed working tree")
    if meta.get("run_id") != run or summary.get("run_id") != run:
        raise bad("its records name a different run id")
    if meta.get("split") != split or summary.get("split") != split:
        raise bad(f"its records do not say it is a {split} run")
    repairer = summary.get("repairer")
    if repairer != meta.get("system"):
        raise bad("the summary's repairer is not the run's system")
    commit = meta.get("commit")
    if not isinstance(commit, str) or not commit:
        raise bad("no commit is recorded")
    for what, by in (("run", summary.get("decided_by")), *_family_deciders(summary)):
        if not isinstance(by, dict):
            raise bad(f"{what}: who decided is not recorded")
        if set(by) - {repairer, "code", "rule"}:
            raise bad(f"{what}: decided by {sorted(set(by) - {repairer, 'code', 'rule'})}")
    if not isinstance(summary.get("repairer_decided"), dict):
        raise bad("the repairer-decided rates are not recorded")


def _family_deciders(summary: dict[str, Any]) -> list[tuple[str, Any]]:
    return [
        (f"family {name}", fam.get("decided_by"))
        for name, fam in summary.get("per_family", {}).items()
    ]


def _rule_rates(run: str, overall: dict[str, Any], repairer: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in RATES:
        whole, part = overall.get(key) or {}, repairer.get(key) or {}
        correct = whole.get("correct", 0) - part.get("correct", 0)
        of = whole.get("of", 0) - part.get("of", 0)
        if correct < 0 or of < 0 or correct > of:
            raise ValueError(f"published run {run}: the repairer decided more {key} cases than all")
        out[key] = {"correct": correct, "of": of, "rate": round(correct / of, 4)} if of else None
    return out


def _families(run: str, summary: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, fam in summary["per_family"].items():
        mine = [f for f in summary["failures"] if f.get("family") == name]
        if any(not isinstance(f.get("decided_by"), str) for f in mine):
            raise ValueError(f"published run {run}: a failure in {name} has no decider")
        rule_cases = fam["decided_by"].get("rule", 0)
        repairer_cases = sum(n for by, n in fam["decided_by"].items() if by != "rule")
        rule_wrong = sum(f["decided_by"] == "rule" for f in mine)
        repairer_wrong = len(mine) - rule_wrong
        if rule_wrong > rule_cases or repairer_wrong > repairer_cases:
            raise ValueError(f"published run {run}: family {name} has more failures than cases")
        out[name] = {
            **fam,
            "rule_cases": rule_cases,
            "rule_correct": rule_cases - rule_wrong,
            "repairer_cases": repairer_cases,
            "repairer_correct": repairer_cases - repairer_wrong,
        }
    return out


def _summary(run: str, summary: dict[str, Any]) -> m.RepairSummary:
    keep = set(m.RepairSummary.model_fields) - {"rule_decided", "repairer_decided"}
    shown = {k: v for k, v in summary.items() if k in keep}
    decided = {
        k: summary["repairer_decided"].get(k) for k in RATES if k in summary["repairer_decided"]
    }
    shown["repairer_decided"] = decided
    shown["rule_decided"] = _rule_rates(run, summary, decided)
    shown["per_family"] = _families(run, summary)
    try:
        return m.RepairSummary.model_validate(shown)
    except ValidationError as e:
        raise ValueError(f"published run {run}: {e}") from e


def _compare(
    root: Path, name: str, repairers: dict[str, str], split_of: dict[str, str]
) -> m.Compare:
    if not RUN_NAME.match(name.removesuffix(".json")):
        raise ValueError(f"{MANIFEST}: compare {name!r} is not a file name")
    data = _read(root / "results" / name, f"compare {name}")
    a, b = str(data.get("a")), str(data.get("b"))
    if a == b or repairers.get(a) != "agent" or repairers.get(b) != "baseline":
        raise ValueError(f"compare {name}: a must be the listed agent run and b the baseline's")
    if split_of[a] != "test" or split_of[b] != "test":
        raise ValueError(f"compare {name}: both runs must be listed test runs")
    keep = {"a", "b", "repair_success", "resamples", "seed", "note"}
    try:
        return m.Compare.model_validate({k: v for k, v in data.items() if k in keep})
    except ValidationError as e:
        raise ValueError(f"compare {name}: {e}") from e


def repairs(root: Path) -> m.Repairs:
    path = root / "results" / MANIFEST
    if not path.is_file():
        return m.Repairs(summaries=[], agent_run=False, compare=None)
    manifest = _read(path, MANIFEST)
    rows: list[m.RepairSummary] = []
    commits: dict[str, str] = {}
    for split, run in _listed(manifest):
        folder = root / "results" / run
        meta = _read(folder / "meta.json", f"published run {run}")
        summary = _read(folder / "summary.json", f"published run {run}")
        _check_run(run, split, meta, summary)
        commits[run] = meta["commit"]
        rows.append(_summary(run, summary))
    for split in SPLITS:
        seen = {
            s.run_id: (s.detection_coverage_fault_cases, s.detection_coverage_benign_control)
            for s in rows
            if s.split == split
        }
        if len({str(v) for v in seen.values()}) > 1:
            raise ValueError(f"published {split} runs differ in what the checks noticed: {seen}")
    if len(set(commits.values())) > 1:
        raise ValueError(f"published runs come from different commits: {commits}")
    name = manifest.get("compare")
    compare = None
    if name is not None:
        if not isinstance(name, str):
            raise ValueError(f"{MANIFEST}: compare must be a file name")
        compare = _compare(
            root,
            name,
            {s.run_id: s.repairer for s in rows},
            {s.run_id: s.split for s in rows},
        )
    return m.Repairs(
        summaries=rows, agent_run=any(s.repairer != "baseline" for s in rows), compare=compare
    )
