from pathlib import Path

import pytest

from pothole.spec.model import PipelineSpec
from pothole.store import db

PINNED_SPEC = Path(__file__).parents[2] / "fixtures" / "batches" / "spec.yaml"


@pytest.fixture
def spec() -> PipelineSpec:
    return PipelineSpec.load(PINNED_SPEC)


@pytest.fixture
def con(tmp_path: Path):  # type: ignore[no-untyped-def]
    connection = db.open(tmp_path / "raw.duckdb")
    yield connection
    connection.close()
