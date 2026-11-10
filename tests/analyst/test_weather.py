from datetime import date

import httpx
import pytest

from pothole.analyst import weather


def client(body: dict, seen: list[httpx.Request]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def body(day: str, precip: object, tmax: object) -> dict:
    return {"daily": {"time": [day], "precipitation_sum": [precip], "temperature_2m_max": [tmax]}}


def test_a_day_six_days_old_comes_from_the_archive() -> None:
    seen: list[httpx.Request] = []
    http = client(body("2026-11-04", 1.5, 12.0), seen)
    got = weather.daily(date(2026, 11, 4), today=date(2026, 11, 10), http=http)
    assert (got.precip_mm, got.tmax_c, got.source) == (1.5, 12.0, "archive")
    assert seen[0].url.host == "archive-api.open-meteo.com"
    assert seen[0].url.params["start_date"] == "2026-11-04"
    assert seen[0].url.params["timezone"] == "America/New_York"


def test_a_newer_day_comes_from_the_forecast_endpoint_with_past_days() -> None:
    seen: list[httpx.Request] = []
    http = client(body("2026-11-05", 0.0, 9.9), seen)
    got = weather.daily(date(2026, 11, 5), today=date(2026, 11, 10), http=http)
    assert got.source == "forecast_past_days"
    assert seen[0].url.host == "api.open-meteo.com"
    assert seen[0].url.params["past_days"] == "6"


def test_weather_missing_stays_missing() -> None:
    http = client(body("2026-11-05", None, None), [])
    got = weather.daily(date(2026, 11, 5), today=date(2026, 11, 10), http=http)
    assert (got.precip_mm, got.tmax_c) == (None, None)


def test_a_response_without_the_day_is_unavailable() -> None:
    http = client(body("2026-11-04", 0.0, 1.0), [])
    with pytest.raises(weather.WeatherUnavailable):
        weather.daily(date(2026, 11, 5), today=date(2026, 11, 10), http=http)


@pytest.mark.live_api
def test_live_recent_day() -> None:
    got = weather.daily(date.today().replace(day=1))
    assert got.source in ("archive", "forecast_past_days")
