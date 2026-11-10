from pathlib import Path

import pytest

from pothole.spec.model import PipelineSpec
from pothole.store import db

SPEC_PATH = Path(__file__).parents[2] / "pipelines" / "311.yaml"


@pytest.fixture
def spec() -> PipelineSpec:
    return PipelineSpec.load(SPEC_PATH)


@pytest.fixture
def con(tmp_path: Path):  # type: ignore[no-untyped-def]
    connection = db.open(tmp_path / "raw.duckdb")
    yield connection
    connection.close()
