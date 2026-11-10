import pytest
from helpers import PINNED_SPEC
from pydantic import ValidationError

from pothole.spec.model import PipelineSpec


def test_spec_round_trips_yaml(spec):
    again = PipelineSpec.from_yaml(spec.to_yaml())
    assert again == spec
    assert len(spec.columns) == 16
    assert spec.checks.volume.model_dump() == {"weeks": 4, "low": 0.5, "high": 2.0}
    assert spec.checks.ranges == {"latitude": (40.49, 40.92), "longitude": (-74.27, -73.68)}


def test_unknown_keys_are_refused():
    text = PINNED_SPEC.read_text() + "extra: 1\n"
    with pytest.raises(ValidationError):
        PipelineSpec.from_yaml(text)


def test_check_naming_an_unknown_column_is_refused():
    text = PINNED_SPEC.read_text().replace("    latitude: 0.2", "    nope: 0.2")
    with pytest.raises(ValidationError, match="unknown column 'nope'"):
        PipelineSpec.from_yaml(text)


def test_required_column_cannot_have_a_null_rate_limit():
    text = PINNED_SPEC.read_text().replace(
        "    latitude: 0.2", "    agency: 0.0\n    latitude: 0.2"
    )
    with pytest.raises(ValidationError, match="required column 'agency'"):
        PipelineSpec.from_yaml(text)


def test_two_columns_cannot_share_a_source():
    text = PINNED_SPEC.read_text().replace(
        "{name: descriptor, source: descriptor,", "{name: descriptor, source: agency,"
    )
    with pytest.raises(ValidationError, match="feeds more than one column"):
        PipelineSpec.from_yaml(text)
