import pytest
from helpers import PINNED_SPEC, make_row

from pothole.spec.model import PipelineSpec


@pytest.fixture
def spec() -> PipelineSpec:
    return PipelineSpec.load(PINNED_SPEC)


@pytest.fixture
def rows() -> list[dict[str, str]]:
    return [make_row(i) for i in range(10)]
