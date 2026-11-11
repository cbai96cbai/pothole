"""Agent minus baseline, case by case, with an interval that treats each family as one draw."""

import json
import random
from collections.abc import Callable
from pathlib import Path
from typing import Any

RESAMPLES = 10_000
SEED = 7
FEW_FAMILIES = 10
MEASURES = (
    ("repair_success", "patch"),
    ("issue_accuracy", "issue"),
    ("no_change_accuracy", "no_change"),
)


def load_verdicts(run_dir: Path) -> dict[str, dict[str, Any]]:
    path = run_dir / "verdicts.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"no verdicts in {run_dir}")
    records = [r for r in map(json.loads, path.read_text().splitlines()) if r]
    seen: set[str] = set()
    for r in records:
        if r["id"] in seen:
            raise ValueError(f"{run_dir.name} lists a case twice: {r['id']}")
        seen.add(r["id"])
    return {r["id"]: r for r in records}


def _difference(a: dict[str, Any], b: dict[str, Any], ids: list[str]) -> dict[str, Any] | None:
    if not ids:
        return None
    ra = sum(a[i]["correct"] for i in ids) / len(ids)
    rb = sum(b[i]["correct"] for i in ids) / len(ids)
    return {
        "cases": len(ids),
        "a": round(ra, 4),
        "b": round(rb, 4),
        "difference": round(ra - rb, 4),
    }


def bootstrap(per_family: dict[str, list[int]]) -> tuple[float, float]:
    """95% interval for the mean paired difference when whole families are resampled."""
    rng = random.Random(SEED)
    names = sorted(per_family)
    means = []
    for _ in range(RESAMPLES):
        drawn = [per_family[rng.choice(names)] for _ in names]
        n = sum(len(d) for d in drawn)
        means.append(sum(sum(d) for d in drawn) / n)
    means.sort()
    return means[int(0.025 * RESAMPLES)], means[int(0.975 * RESAMPLES) - 1]


def _catalog_ids(split: str) -> set[str]:
    from pothole.drills import catalog

    return {c.id for c in catalog.load() if c.split == split}


def _complete(run_dir: Path, case_ids: Callable[[str], set[str]]) -> str:
    """The run's split, or an error when it has no summary or its verdicts are not exactly the
    split's cases."""
    path = run_dir / "summary.json"
    if not path.exists():
        raise ValueError(f"{run_dir.name} is not finished: no summary.json")
    summary = json.loads(path.read_text())
    have, want = set(load_verdicts(run_dir)), case_ids(summary["split"])
    if missing := sorted(want - have):
        raise ValueError(
            f"{run_dir.name} is incomplete: {len(have)} of {len(want)} cases; missing {missing[0]}"
        )
    if extra := sorted(have - want):
        raise ValueError(f"{run_dir.name} has a case that is not in the split: {extra[0]}")
    if summary["cases"] != len(want):
        raise ValueError(
            f"{run_dir.name} is incomplete: its summary counts {summary['cases']} cases"
        )
    return str(summary["split"])


def compare(
    a_dir: Path, b_dir: Path, case_ids: Callable[[str], set[str]] = _catalog_ids
) -> dict[str, Any]:
    """Two runs, each holding exactly the split's cases once, so every case pairs."""
    splits = {_complete(a_dir, case_ids), _complete(b_dir, case_ids)}
    if len(splits) > 1:
        raise ValueError(f"the runs are on different splits: {', '.join(sorted(splits))}")
    a, b = load_verdicts(a_dir), load_verdicts(b_dir)
    paired = sorted(set(a) & set(b))
    body: dict[str, Any] = {
        "a": a_dir.name,
        "b": b_dir.name,
        "paired_cases": len(paired),
        "unpaired": {"a_only": len(set(a) - set(b)), "b_only": len(set(b) - set(a))},
    }
    for name, expected in MEASURES:
        ids = [i for i in paired if a[i]["expected"] == expected]
        body[name] = _difference(a, b, ids)
    patch = [i for i in paired if a[i]["expected"] == "patch"]
    if patch:
        by_family: dict[str, list[int]] = {}
        for i in patch:
            by_family.setdefault(a[i]["family"], []).append(
                int(a[i]["correct"]) - int(b[i]["correct"])
            )
        low, high = bootstrap(by_family)
        body["repair_success"]["interval_95"] = [round(low, 4), round(high, 4)]
        body["repair_success"]["families"] = len(by_family)
        body["resamples"], body["seed"] = RESAMPLES, SEED
        if len(by_family) < FEW_FAMILIES:
            body["note"] = (
                f"only {len(by_family)} families: the interval is wide and should be read loosely"
            )
    return body


def render(body: dict[str, Any]) -> str:
    lines = [f"{body['a']} minus {body['b']}: {body['paired_cases']} cases paired"]
    for name, _ in MEASURES:
        m = body[name]
        if m is None:
            lines.append(f"{name:20} no cases")
            continue
        text = (
            f"{name:20} {m['a']:.3f} vs {m['b']:.3f}  "
            f"difference {m['difference']:+.3f} (n={m['cases']})"
        )
        if "interval_95" in m:
            low, high = m["interval_95"]
            text += f"  95% interval {low:+.3f} to {high:+.3f}"
        lines.append(text)
    if "note" in body:
        lines.append(body["note"])
    return "\n".join(lines)
