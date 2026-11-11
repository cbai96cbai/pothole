"""The drill catalog: which faults, on which recorded days, scored in which split.

The catalog is built once by a fixed rule and frozen in data/drills/catalog.json. Nothing here
is reachable from the repair agent's tools: it sees only the faulted rows of `batch_seen`.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import cache
from pathlib import Path
from typing import Any, Literal, cast

from pothole.drills.faults import Fault, Row, inject
from pothole.socrata import batches
from pothole.spec import engine
from pothole.spec.model import PipelineSpec

ROOT = Path(__file__).parents[3]
CATALOG = ROOT / "data" / "drills" / "catalog.json"
FREEZE = ROOT / "data" / "drills" / "freeze.json"
BATCH_DIR = ROOT / "fixtures" / "batches"
SPEC_PATH = BATCH_DIR / "spec.yaml"  # the spec the batches were recorded under
JUDGED_DAYS = 3
Expected = Literal["patch", "issue", "no_change", "nothing"]


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
    params: dict[str, Any]


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


def engine_time(day: str) -> datetime:
    """When the engine reads a recorded day: 30 hours after the day ends, so the day is stale."""
    return datetime.strptime(day, "%Y-%m-%d") + timedelta(days=1, hours=30)


def run_engine(spec: PipelineSpec, rows: list[Row], day: str) -> engine.EngineResult:
    """The engine on one recorded day, at `engine_time`."""
    return engine.run(spec, cast(list[dict[str, str]], rows), history(), engine_time(day))


def load_spec() -> PipelineSpec:
    return PipelineSpec.load(SPEC_PATH)


def fault_of(case: Case) -> Fault:
    return Fault(case.family, case.variant, case.seed, case.params)


def seen_batch(case: Case) -> tuple[list[Row], dict[str, str]]:
    """What the pipeline received on the case's day: the faulted rows and the source fields."""
    return inject(clean_batch(case.batch_seen), fault_of(case))


def load(path: Path = CATALOG) -> list[Case]:
    raw = json.loads(path.read_text())
    return [Case(**{**c, "batch_judged": tuple(c["batch_judged"])}) for c in raw["cases"]]
