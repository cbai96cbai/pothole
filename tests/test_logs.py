import json

import pytest

from pothole.logs import get_logger


def test_one_json_object_per_line_with_extras(capsys: pytest.CaptureFixture[str]) -> None:
    log = get_logger("pothole.test")
    log.info("loaded", extra={"rows": 5})
    log.info("again")
    lines = capsys.readouterr().err.splitlines()
    first = json.loads(lines[0])
    assert first["message"] == "loaded" and first["rows"] == 5
    assert first["level"] == "INFO" and first["logger"] == "pothole.test" and "ts" in first
    assert len(lines) == 2  # asking for the logger twice adds no second handler
