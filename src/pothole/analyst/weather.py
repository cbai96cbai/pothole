"""Daily weather at Central Park from Open-Meteo (CC BY 4.0), New York local dates.

A day at least 6 days old comes from the archive endpoint; a newer one from the forecast
endpoint with past_days, because the archive lags by a few days. A missing value stays None.
"""

from datetime import date
from typing import Any

import httpx

from pothole.analyst.finding import Weather, WeatherSource
from pothole.logs import get_logger

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
POINT = {"latitude": "40.7812", "longitude": "-73.9665"}
ARCHIVE_MIN_AGE_DAYS = 6
TIMEOUT_S = 30.0

log = get_logger(__name__)


class WeatherUnavailable(Exception):
    """Open-Meteo did not give a usable answer."""


def _value(daily: dict[str, Any], name: str, index: int) -> float | None:
    values = daily.get(name)
    if not isinstance(values, list) or index >= len(values):
        return None
    v = values[index]
    return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else None


def daily(day: date, *, today: date | None = None, http: httpx.Client | None = None) -> Weather:
    today = today or date.today()
    age = (today - day).days
    source: WeatherSource = "archive" if age >= ARCHIVE_MIN_AGE_DAYS else "forecast_past_days"
    params = {
        **POINT,
        "daily": "precipitation_sum,temperature_2m_max",
        "timezone": "America/New_York",
    }
    if source == "archive":
        url = ARCHIVE_URL
        params |= {"start_date": day.isoformat(), "end_date": day.isoformat()}
    else:
        url = FORECAST_URL
        params |= {"past_days": str(max(age, 0) + 1), "forecast_days": "1"}
    client = http or httpx.Client(timeout=TIMEOUT_S)
    try:
        response = client.get(url, params=params)
        response.raise_for_status()
        body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise WeatherUnavailable(f"{source}: {type(exc).__name__}: {exc}") from exc
    finally:
        if http is None:
            client.close()
    block = body.get("daily") if isinstance(body, dict) else None
    times = block.get("time") if isinstance(block, dict) else None
    if not isinstance(block, dict) or not isinstance(times, list) or day.isoformat() not in times:
        raise WeatherUnavailable(f"{source}: no entry for {day.isoformat()}")
    i = times.index(day.isoformat())
    return Weather(
        _value(block, "precipitation_sum", i), _value(block, "temperature_2m_max", i), source
    )
