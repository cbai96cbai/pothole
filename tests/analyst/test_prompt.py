from pothole.analyst.prompt import SYSTEM_PROMPT, TOOLS
from pothole.analyst.tools import EVENTS_SCOPE


def test_the_model_is_told_what_the_events_list_is() -> None:
    events = next(t for t in TOOLS if t["function"]["name"] == "events")
    for text in (SYSTEM_PROMPT, events["function"]["description"]):
        assert "list the district" in text or "list this district" in text
        assert "not mean activity throughout" in text
        assert "in the district that day" not in text
    assert "not mean activity throughout" in EVENTS_SCOPE


def test_the_model_is_told_a_day_before_the_data_is_not_a_zero() -> None:
    series = next(t for t in TOOLS if t["function"]["name"] == "series")
    assert "data starts" in series["function"]["description"]
