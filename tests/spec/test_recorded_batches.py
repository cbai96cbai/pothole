from datetime import datetime, timedelta
from pathlib import Path

import pytest

from pothole.socrata import batches
from pothole.spec import engine
from pothole.spec.model import PipelineSpec

BATCHES = sorted((Path(__file__).parents[2] / "fixtures" / "batches").glob("*.jsonl.gz"))


def test_the_six_recorded_days_are_there():
    assert len(BATCHES) == 6


@pytest.mark.parametrize("path", BATCHES, ids=lambda p: p.name)
def test_311_spec_passes_on_recorded_batch(path: Path):
    spec = PipelineSpec.load(BATCHES[0].parent / "spec.yaml")  # the spec as it was when recorded
    day = datetime.strptime(path.name.split(".")[0], "%Y-%m-%d")
    now = day + timedelta(days=1, hours=30)  # the batch date's end, plus 30 hours
    result = engine.run(spec, batches.load(path), [], now)
    assert [c for c in result.checks if c.level == "fail"] == []
    assert result.table.num_rows == 2000
