"""Command line: `pothole <command>`."""

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any
from zoneinfo import ZoneInfo

import typer

from pothole.config import Settings
from pothole.socrata import batches
from pothole.socrata.client import Socrata

app = typer.Typer(no_args_is_help=True, add_completion=False)

NEW_YORK = ZoneInfo("America/New_York")
DATASET_311 = "erm2-nwe9"
BATCH_ROWS = 2000
BATCH_DAYS = 6
FIRST_OFFSET = 3  # newest batch day is run date - 3; the oldest is run date - 8


@app.callback()
def main() -> None:
    """A daily pipeline over public NYC 311 data."""


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


@app.command("record-batches")
def record_batches(
    out: Annotated[Path, typer.Option(help="Folder for <date>.jsonl.gz files.")] = Path(
        "fixtures/batches"
    ),
) -> None:
    """Save 2,000 raw 311 rows for each of 6 recent New York days as test batches."""
    client = Socrata(token=Settings.from_env().socrata_app_token)
    today = datetime.now(NEW_YORK).date()
    out.mkdir(parents=True, exist_ok=True)
    for offset in range(FIRST_OFFSET, FIRST_OFFSET + BATCH_DAYS):
        day = today - timedelta(days=offset)
        rows = fetch_batch(client, day)
        if len(rows) < BATCH_ROWS:
            typer.echo(f"{day}: only {len(rows)} rows, not a complete day", err=True)
            raise typer.Exit(1)
        path = out / f"{day}.jsonl.gz"
        batches.dump(path, rows)
        typer.echo(f"{path}: {len(rows)} rows")
