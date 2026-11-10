"""Every committed regression case: the spec it names must turn its rows into its expected rows."""

import json
from pathlib import Path

import pytest

from pothole.repair.outcome import RegressionCase
from pothole.repair.validate import reproduces
from pothole.spec.model import PipelineSpec

ROOT = Path(__file__).parents[2]
CASES = sorted(Path(__file__).parent.glob("*.json"))


@pytest.mark.parametrize("path", CASES, ids=lambda p: p.name)
def test_regression_case_reproduces(path: Path) -> None:
    data = json.loads(path.read_text())
    spec = PipelineSpec.load(ROOT / data["spec"])
    assert reproduces(spec, RegressionCase(data["rows"], data["expected"]))
