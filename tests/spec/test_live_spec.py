"""One of the two tests that read the live spec (the other is the regression collector)."""

from pathlib import Path

from pothole.repair.render import SPEC_PATH
from pothole.spec.model import PipelineSpec


def test_live_spec_parses_and_validates() -> None:
    spec = PipelineSpec.load(Path(__file__).parents[2] / SPEC_PATH)
    assert spec.columns and spec.source.key in {c.name for c in spec.columns}
