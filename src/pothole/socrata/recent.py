"""A day of raw 311 rows as the source sends them (used by the batch recorder and the daily run)."""

from datetime import date, timedelta
from typing import Any

from pothole.socrata.client import Socrata

DATASET_311 = "erm2-nwe9"
BATCH_ROWS = 2000


def fetch_batch(client: Socrata, day: date) -> list[dict[str, Any]]:
    """BATCH_ROWS rows created on `day`, by unique_key, all fields plus :id and :updated_at."""
    nxt = day + timedelta(days=1)
    return client.query(
        DATASET_311,
        {
            "$select": "*, :id, :updated_at",
            "$where": f"created_date >= '{day}T00:00:00' AND created_date < '{nxt}T00:00:00'",
            "$order": "unique_key",
            "$limit": str(BATCH_ROWS),
        },
    )
