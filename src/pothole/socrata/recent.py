"""A day of raw 311 rows as the source sends them (used by the batch recorder and the daily run)."""

from datetime import date, timedelta
from typing import Any

from pothole.socrata.client import Socrata, SourceRejected

DATASET_311 = "erm2-nwe9"
BATCH_ROWS = 2000
ALL_FIELDS = "*, :id, :updated_at"
ALL_FIELDS_WITH_ARRIVAL = "*, :id, :updated_at, :created_at"


def _day(client: Socrata, day: date, select: str) -> list[dict[str, Any]]:
    nxt = day + timedelta(days=1)
    return client.query(
        DATASET_311,
        {
            "$select": select,
            "$where": f"created_date >= '{day}T00:00:00' AND created_date < '{nxt}T00:00:00'",
            "$order": "unique_key",
            "$limit": str(BATCH_ROWS),
        },
    )


def fetch_batch(client: Socrata, day: date) -> list[dict[str, Any]]:
    """BATCH_ROWS rows created on `day`, by unique_key, all fields plus :id and :updated_at."""
    return _day(client, day, ALL_FIELDS)


def fetch_schema_sample(client: Socrata, day: date) -> list[dict[str, Any]]:
    """Rows as the source sends them now, for diagnosing a changed schema: every field plus the
    system fields, for `day` by unique_key. If the source rejects that query because it names a
    field the dataset no longer has (the day or the key field itself was renamed), the newest
    BATCH_ROWS rows by the system field :updated_at are taken instead."""
    try:
        return _day(client, day, ALL_FIELDS_WITH_ARRIVAL)
    except SourceRejected as exc:
        if not exc.names_missing_column:
            raise
    return client.query(
        DATASET_311,
        {
            "$select": ALL_FIELDS_WITH_ARRIVAL,
            "$order": ":updated_at DESC",
            "$limit": str(BATCH_ROWS),
        },
    )
