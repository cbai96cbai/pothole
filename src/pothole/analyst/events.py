"""City-permitted events (NYC Open Data tvpp-9vvx) that overlap a day in one community district."""

from datetime import date

from pothole.analyst.finding import Event
from pothole.socrata.client import Socrata
from pothole.spec.normalise import event_boards

DATASET = "tvpp-9vvx"
LIMIT = 5000


def _quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def on(borough: str, board: int, day: date, client: Socrata | None = None) -> list[Event]:
    """Events in `borough` whose time span touches `day` and whose community boards include
    the district. Event rows list boards as text such as "1,2,"."""
    client = client or Socrata()
    rows = client.query(
        DATASET,
        {
            "$where": f"event_borough = {_quote(borough)} "
            f"AND start_date_time <= '{day.isoformat()}T23:59:59' "
            f"AND end_date_time >= '{day.isoformat()}T00:00:00'",
            "$select": "event_name, event_type, start_date_time, end_date_time, "
            "event_borough, community_board",
            "$order": "start_date_time, event_name",
            "$limit": str(LIMIT),
        },
    )
    found: list[Event] = []
    for row in rows:
        if (borough, board) not in event_boards([row.get("community_board")], borough):
            continue
        found.append(
            Event(
                str(row.get("event_name", "")),
                str(row.get("event_type", "")),
                str(row.get("start_date_time", "")),
                str(row.get("end_date_time", "")),
            )
        )
    return found
