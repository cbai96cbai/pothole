import pytest

from pothole.drills import catalog
from pothole.drills.catalog import Case
from pothole.spec.model import PipelineSpec


@pytest.fixture(scope="session")
def spec() -> PipelineSpec:
    return catalog.load_spec()


@pytest.fixture(scope="session")
def cases() -> dict[str, Case]:
    return {c.id: c for c in catalog.load()}
