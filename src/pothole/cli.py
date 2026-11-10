"""Command line: `pothole <command>`."""

import json
import os
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any
from zoneinfo import ZoneInfo

import typer

from pothole.analyst import null_simulation
from pothole.analyst.finding import to_json
from pothole.config import Settings
from pothole.dailyai import Sink
from pothole.repair.sink import GitHubSink, LocalSink
from pothole.repair.validate import failed, load_batches
from pothole.socrata import batches
from pothole.socrata.client import Socrata, SourceRejected, SourceUnavailable
from pothole.spec import engine
from pothole.spec.model import PipelineSpec
from pothole.store import db, load

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


@app.command("load")
def load_command(
    spec_path: Annotated[Path, typer.Option("--spec", help="The pipeline spec.")] = Path(
        "pipelines/311.yaml"
    ),
) -> None:
    """Load what changed in the 311 window into the local store (all of it on a cache miss)."""
    settings = Settings.from_env()
    spec = PipelineSpec.load(spec_path)
    client = Socrata(spec.source.domain, settings.socrata_app_token)
    now = datetime.now(NEW_YORK).replace(tzinfo=None)
    con = db.open(settings.state / "raw.duckdb")
    try:
        result = load.incremental(con, client, spec, now)
    except (SourceUnavailable, SourceRejected) as exc:
        typer.echo(f"load stopped: {exc}", err=True)
        raise typer.Exit(1) from exc
    finally:
        con.close()
    typer.echo(f"rows {result.rows} pages {result.pages} cursor {result.cursor}")
    for check in result.checks:
        typer.echo(f"{check.level:4} {check.name}: {check.detail}")


@app.command("check-spec")
def check_spec(
    spec: Annotated[Path, typer.Option(help="The pipeline spec to run.")],
    batches_dir: Annotated[Path, typer.Option("--batches", help="Folder of <date>.jsonl.gz.")],
) -> None:
    """Run the spec on every recorded batch; exit 1 when any check fails."""
    pipeline = PipelineSpec.load(spec)
    bad = 0
    for batch in load_batches(batches_dir):
        fails = failed(engine.run(pipeline, batch.rows, [], batch.now).checks)
        typer.echo(f"{batch.name}: {'FAIL' if fails else 'ok'}")
        for res in fails:
            typer.echo(f"  {res.name}: {res.detail}")
        bad += bool(fails)
    if bad:
        raise typer.Exit(1)


@app.command("null-simulation")
def null_simulation_command() -> None:
    """Measure how often the detector flags something on simulated days with no events."""
    body = null_simulation.run()
    typer.echo(json.dumps(body["share_of_days_with_a_candidate"]))


def _sink(settings: Settings, repo: Path) -> Sink:
    """Pull requests and issues on GitHub when the workflow's token is there, else files."""
    token, slug = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if token and slug:
        return GitHubSink(slug, token, repo)
    return LocalSink(settings.state / "sink")


@app.command("daily")
def daily_command(
    repo: Annotated[Path, typer.Option(help="The repository checkout to read and write.")] = Path(
        "."
    ),
    site_dir: Annotated[Path | None, typer.Option(help="Where the site data goes.")] = None,
) -> None:
    """The daily run. Model steps run only when AI_ENABLED=true."""
    from pothole import daily
    from pothole.budget.envelope import EnvelopeExceeded
    from pothole.budget.tally import Tally
    from pothole.llm.client import LLMClient

    settings = Settings.from_env()
    ai = os.environ.get("AI_ENABLED") == "true"
    if ai and not (settings.azure_endpoint and settings.azure_api_key):
        typer.echo("AI_ENABLED is true but the Azure endpoint or key is not set", err=True)
        raise typer.Exit(1)
    on_github = os.environ.get("GITHUB_ACTIONS") == "true"
    repo = repo.resolve()
    env = daily.Env(
        repo=repo,
        state=settings.state,
        socrata=Socrata(token=settings.socrata_app_token),
        sink=_sink(settings, repo),
        client=LLMClient(settings) if ai else None,
        ai_enabled=ai,
        tally=Tally() if ai and not on_github else None,
        run_id=os.environ.get("GITHUB_RUN_ID") or uuid.uuid4().hex,
        site_dir=site_dir,
    )
    try:
        record = daily.run(env)
    except EnvelopeExceeded as exc:
        typer.echo(f"refusing to run: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(json.dumps(to_json(record), sort_keys=True))


@app.command("tally-github")
def tally_github_command(
    repo: Annotated[str, typer.Option(help="owner/name on GitHub.")] = "cbai96cbai/pothole",
    since: Annotated[datetime, typer.Option(formats=["%Y-%m-%d"], help="First day to count.")] = (
        datetime(2026, 11, 10)
    ),
    records: Annotated[Path, typer.Option(help="The committed run records.")] = Path(
        "data/runs.jsonl"
    ),
) -> None:
    """Admit each finished GitHub run that had AI enabled into the build tally, once."""
    from pothole.budget.tally import Tally
    from pothole.tallygithub import tally_github

    done = tally_github(repo, since.date(), records, Tally())
    typer.echo(
        f"admitted {len(done.admitted)}, already {len(done.already)}, "
        f"no committed record {len(done.no_record)}"
    )
