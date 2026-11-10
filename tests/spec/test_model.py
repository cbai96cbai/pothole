import pytest
from helpers import SPEC_PATH
from pydantic import ValidationError

from pothole.spec.model import PipelineSpec


def test_spec_round_trips_yaml(spec):
    again = PipelineSpec.from_yaml(spec.to_yaml())
    assert again == spec
    assert len(spec.columns) == 16
    assert spec.checks.volume.model_dump() == {"weeks": 4, "low": 0.5, "high": 2.0}
    assert spec.checks.ranges == {"latitude": (40.49, 40.92), "longitude": (-74.27, -73.68)}


def test_unknown_keys_are_refused():
    text = SPEC_PATH.read_text() + "extra: 1\n"
    with pytest.raises(ValidationError):
        PipelineSpec.from_yaml(text)


def test_check_naming_an_unknown_column_is_refused():
    text = SPEC_PATH.read_text().replace("    latitude: 0.2", "    nope: 0.2")
    with pytest.raises(ValidationError, match="unknown column 'nope'"):
        PipelineSpec.from_yaml(text)
