"""The drill catalog: which faults, on which recorded days, scored in which split.

The catalog is built once by a fixed rule and frozen in data/drills/catalog.json. Nothing here
is reachable from the repair agent's tools: it sees only the faulted rows of `batch_seen`.
"""

import json
import random
import zlib
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from functools import cache
from pathlib import Path
from typing import Any, Literal, cast

from pothole.drills.faults import FAMILY_NAMES, Fault, Row, inject
from pothole.socrata import batches
from pothole.spec import engine
from pothole.spec.model import PipelineSpec

ROOT = Path(__file__).parents[3]
CATALOG = ROOT / "data" / "drills" / "catalog.json"
BATCH_DIR = ROOT / "fixtures" / "batches"
SPEC_PATH = BATCH_DIR / "spec.yaml"  # the spec the batches were recorded under
SAMPLE_ROWS = 200
JUDGED_DAYS = 3
RULE = (
    "seed = crc32('<family>:<variant>'). The six recorded days are shuffled by "
    "random.Random('pothole-drills:<family>'); variant v is seen on day v mod 6 of that order; "
    "three of the other five days, drawn by random.Random(seed) and sorted, are judged."
)

Expected = Literal["patch", "issue", "no_change", "nothing"]
# family -> (right action, dev variants, test variants). Controls have nine variants.
FAMILIES: dict[str, tuple[Expected, range, range]] = {
    "renamed_field": ("patch", range(6), range(0)),
    "date_format": ("patch", range(6), range(0)),
    "borough_recoding": ("patch", range(6), range(0)),
    "comma_thousands": ("patch", range(0), range(6)),
    "latlon_swapped": ("patch", range(0), range(6)),
    "field_dropped": ("issue", range(6), range(0)),
    "partial_load": ("issue", range(6), range(0)),
    "null_flood": ("issue", range(0), range(6)),
    "source_empty": ("issue", range(0), range(6)),
    "spike": ("no_change", range(6), range(6, 9)),
    "benign_field": ("nothing", range(6), range(6, 9)),
}


@dataclass(frozen=True)
class Case:
    id: str
    family: str
    variant: int
    seed: int
    batch_seen: str
    batch_judged: tuple[str, ...]
    expected_action: Expected
    split: Literal["dev", "test"]


def days() -> list[str]:
    return sorted(p.name.split(".")[0] for p in BATCH_DIR.glob("*.jsonl.gz"))


@cache
def clean_batch(day: str) -> list[Row]:
    """A recorded day's rows, shared between callers: never change them."""
    return batches.load(BATCH_DIR / f"{day}.jsonl.gz")


@cache
def history() -> list[int]:
    """Same-weekday counts that make the volume band meaningful: the recorded batch sizes."""
    return [len(clean_batch(d)) for d in days()]


def run_engine(spec: PipelineSpec, rows: list[Row], day: str) -> engine.EngineResult:
    """The engine on one recorded day, at the day's end plus 30 hours."""
    now = datetime.strptime(day, "%Y-%m-%d") + timedelta(days=1, hours=30)
    return engine.run(spec, cast(list[dict[str, str]], rows), history(), now)


def load_spec() -> PipelineSpec:
    return PipelineSpec.load(SPEC_PATH)


def fault_of(case: Case) -> Fault:
    return Fault(case.family, case.variant, case.seed)


def seen_batch(case: Case) -> tuple[list[Row], dict[str, str]]:
    """What the pipeline received on the case's day: the faulted rows and the source fields."""
    return inject(clean_batch(case.batch_seen), fault_of(case))


def sample(rows: list[Row], case: Case) -> list[Row]:
    """The rows shown to a repairer: a seeded sample of the faulted batch, in batch order."""
    keep = sorted(random.Random(case.seed).sample(range(len(rows)), min(SAMPLE_ROWS, len(rows))))
    return [rows[i] for i in keep]


def build() -> list[Case]:
    all_days = days()
    cases: list[Case] = []
    for family in FAMILY_NAMES:
        action, dev, test = FAMILIES[family]
        order = list(all_days)
        random.Random(f"pothole-drills:{family}").shuffle(order)
        splits: list[tuple[Literal["dev", "test"], range]] = [("dev", dev), ("test", test)]
        for split, variants in splits:
            for v in variants:
                seed = zlib.crc32(f"{family}:{v}".encode())
                seen = order[v % len(order)]
                others = [d for d in all_days if d != seen]
                judged = sorted(random.Random(seed).sample(others, JUDGED_DAYS))
                case_id = f"{family}-{v}"
                cases.append(Case(case_id, family, v, seed, seen, tuple(judged), action, split))
    return cases


def write(path: Path = CATALOG) -> None:
    body: dict[str, Any] = {"rule": RULE, "cases": [asdict(c) for c in build()]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=2) + "\n")


def load(path: Path = CATALOG) -> list[Case]:
    raw = json.loads(path.read_text())
    return [Case(**{**c, "batch_judged": tuple(c["batch_judged"])}) for c in raw["cases"]]


if __name__ == "__main__":
    write()
