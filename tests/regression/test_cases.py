"""Every committed regression case: the spec it carries must turn its rows into its expected rows.
A case never reads the live spec, so a later rename cannot turn an earlier case red."""

import json
from pathlib import Path

import pytest

from pothole.repair.validate import case_reproduces

CASES = sorted(Path(__file__).parent.glob("*.json"))


@pytest.mark.parametrize("path", CASES, ids=lambda p: p.name)
def test_regression_case_reproduces(path: Path) -> None:
    assert case_reproduces(json.loads(path.read_text()))
