from datetime import date

import httpx
import pytest

from pothole.analyst import events
from pothole.socrata.client import Socrata

ROWS = [
    {
        "event_name": "A",
        "event_type": "Parade",
        "start_date_time": "2026-11-07T09:00:00.000",
        "end_date_time": "2026-11-07T12:00:00.000",
        "event_borough": "Brooklyn",
        "community_board": "3,",
    },
    {
        "event_name": "B",
        "event_type": "Street Event",
        "start_date_time": "2026-11-06T09:00:00.000",
        "end_date_time": "2026-11-08T12:00:00.000",
        "event_borough": "Brooklyn",
        "community_board": "1,3,",
    },
    {
        "event_name": "C",
        "event_type": "Sport",
        "start_date_time": "2026-11-07T09:00:00.000",
        "end_date_time": "2026-11-07T12:00:00.000",
        "event_borough": "Brooklyn",
        "community_board": "30,",
    },
    {
        "event_name": "D",
        "event_type": "Sport",
        "start_date_time": "2026-11-07T09:00:00.000",
        "end_date_time": "2026-11-07T12:00:00.000",
        "event_borough": "Brooklyn",
        "community_board": None,
    },
    {
        "event_name": "E",
        "event_type": "Sport",
        "start_date_time": "2026-11-07T09:00:00.000",
        "end_date_time": "2026-11-07T12:00:00.000",
        "event_borough": "Brooklyn",
        "community_board": "64,",
    },
]


def socrata(seen: list[httpx.Request]) -> Socrata:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=ROWS)

    client = Socrata()
    client._http = httpx.Client(
        base_url="https://data.cityofnewyork.us", transport=httpx.MockTransport(handler)
    )
    return client


def test_events_board_list_parsing() -> None:
    seen: list[httpx.Request] = []
    got = events.on("Brooklyn", 3, date(2026, 11, 7), socrata(seen))
    assert [e.name for e in got] == ["A", "B"]  # "1,3," lists the district; 30, 64 and blank do not
    assert got[0].type == "Parade" and got[0].start == "2026-11-07T09:00:00.000"
    where = seen[0].url.params["$where"]
    assert "event_borough = 'Brooklyn'" in where and "2026-11-07T23:59:59" in where


def test_no_event_in_the_district_is_an_empty_list() -> None:
    assert events.on("Brooklyn", 9, date(2026, 11, 7), socrata([])) == []


@pytest.mark.live_api
def test_live_events_query_runs() -> None:
    assert isinstance(events.on("Manhattan", 1, date.today()), list)


def test_a_full_page_of_rows_is_flagged_as_possibly_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(events, "LIMIT", len(ROWS))
    assert events.on("Brooklyn", 3, date(2026, 11, 7), socrata([])).truncated
    monkeypatch.setattr(events, "LIMIT", len(ROWS) + 1)
    assert not events.on("Brooklyn", 3, date(2026, 11, 7), socrata([])).truncated
