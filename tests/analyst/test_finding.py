import json
from datetime import date
from decimal import Decimal

from pothole.analyst.finding import AnalystDay, Evidence, Finding, Weather, to_json


def test_a_day_serialises_to_json_with_dates_and_decimals_as_strings() -> None:
    f = Finding(
        id="2026-11-07-bronx-9-heat",
        day=date(2026, 11, 7),
        borough="Bronx",
        board=9,
        complaint_type="HEAT/HOT WATER",
        observed=89,
        expected=5.62,
        ratio=15.8,
        q=1e-20,
        summary="Associated with the first cold night of the season.",
        evidence=[Evidence(1, "weather", {}, "tmax 3.1 C")],
        series=[(date(2026, 11, 6), 7), (date(2026, 11, 7), 89)],
        points=[(40.83, -73.86)],
        weather=Weather(0.0, 3.1, "forecast_past_days"),
        events=[],
        status="investigated",
        events_state="complete",
        cost=Decimal("0.031"),
    )
    out = to_json(AnalystDay(date(2026, 11, 7), 1400, 3, [f]))
    assert out["day"] == "2026-11-07"
    assert out["findings"][0]["series"][1] == ["2026-11-07", 89]
    assert out["findings"][0]["cost"] == "0.031"
    assert out["findings"][0]["status"] == "investigated" and out["findings"][0]["notes"] == []
    assert out["findings"][0]["weather"]["source"] == "forecast_past_days"
    json.dumps(out)


def test_a_quiet_day_has_no_findings() -> None:
    out = to_json(AnalystDay(date(2026, 11, 8), 1400, 0, []))
    assert out["findings"] == [] and out["skipped_reason"] is None


def test_a_finding_must_say_whether_its_events_list_is_whole() -> None:
    import pytest

    with pytest.raises(TypeError, match="events_state"):
        Finding(  # type: ignore[call-arg]
            id="x", day=date(2026, 11, 7), borough="Bronx", board=9, complaint_type="T",
            observed=1, expected=1.0, ratio=1.0, q=1.0, summary="", evidence=[], series=[],
            points=[], weather=None, events=[], status="unfinished",
        )  # fmt: skip
