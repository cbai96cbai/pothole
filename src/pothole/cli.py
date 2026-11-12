"""Command line: `pothole <command>`."""

import json
import os
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated
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
from pothole.socrata.recent import BATCH_ROWS, fetch_batch
from pothole.spec import engine
from pothole.spec.model import PipelineSpec
from pothole.store import db, load

app = typer.Typer(no_args_is_help=True, add_completion=False)

NEW_YORK = ZoneInfo("America/New_York")
BATCH_DAYS = 6
FIRST_OFFSET = 3  # newest batch day is run date - 3; the oldest is run date - 8


@app.callback()
def main() -> None:
    """A daily pipeline over public NYC 311 data."""


@app.command("demo")
def demo_command(
    repo: Annotated[Path, typer.Option(help="The repository checkout to read.")] = Path("."),
    out: Annotated[Path | None, typer.Option(help="Where the site data goes.")] = None,
) -> None:
    """Write the site data from the committed files: no key and no network."""
    from pothole import sitedata

    repo = repo.resolve()
    target = out or repo / "web" / "src" / "data"
    sitedata.build(repo, target)
    typer.echo(f"site data written to {target}")
    typer.echo("to build and preview the site (needs Node 24):")
    typer.echo("  cd web && npm ci && npm run build")
    typer.echo("  npx vite preview   # then open http://localhost:4173/pothole/")


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

    repo = repo.resolve()
    run_id = os.environ.get("GITHUB_RUN_ID") or uuid.uuid4().hex
    ai = os.environ.get("AI_ENABLED") == "true"
    attempt = 1
    try:
        attempt = int(os.environ.get("GITHUB_RUN_ATTEMPT") or 1)
        settings = Settings.from_env()
        if ai and not (settings.azure_endpoint and settings.azure_api_key):
            raise ValueError("AI_ENABLED is true but the Azure endpoint or key is not set")
        env = daily.Env(
            repo=repo,
            state=settings.state,
            socrata=Socrata(token=settings.socrata_app_token),
            sink=_sink(settings, repo),
            client=LLMClient(settings) if ai else None,
            ai_enabled=ai,
            tally=Tally() if ai and os.environ.get("GITHUB_ACTIONS") != "true" else None,
            run_id=run_id,
            attempt=attempt,
            site_dir=site_dir,
        )
    except Exception as exc:  # a run that cannot start still leaves its record
        daily.record_setup_failure(repo, run_id, attempt, ai, exc)
        typer.echo(f"the run could not start: {type(exc).__name__}", err=True)
        raise typer.Exit(1) from exc
    try:
        record = daily.run(env)
    except EnvelopeExceeded as exc:
        typer.echo(f"refusing to run: {exc}", err=True)
        raise typer.Exit(1) from exc
    except Exception as exc:  # the run has already written its error record
        typer.echo(f"the run failed and was recorded: {type(exc).__name__}", err=True)
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
    """Charge every scheduled GitHub run attempt that may have used AI to the build tally, once."""
    from pothole.budget.tally import Tally
    from pothole.tallygithub import RunListTruncated, tally_github

    try:
        done = tally_github(repo, since.date(), records, Tally())
    except RunListTruncated as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    typer.echo(
        f"admitted {len(done.admitted)} ({len(done.without_record)} with no committed record), "
        f"already {len(done.already)}, AI off {len(done.ai_off)}"
    )


@app.command("eval-repair")
def eval_repair_command(
    split: Annotated[str, typer.Option(help="dev or test.")],
    system: Annotated[str, typer.Option(help="agent or baseline.")],
    run_id: Annotated[str, typer.Option(help="Names the folder under results/.")],
    after_credit: Annotated[bool, typer.Option(help="Spend after the build credit ended.")] = False,
    rerun_test: Annotated[bool, typer.Option(help="Allow a second agent run on test.")] = False,
    results: Annotated[Path, typer.Option(help="Where run folders go.")] = Path("results"),
    case_timeout: Annotated[float, typer.Option(help="Seconds before a case is wrong.")] = 120.0,
) -> None:
    """Score the repair agent or the non-AI baseline on a drill split; resumes a run id."""
    from datetime import date

    from pothole.budget.tally import Tally
    from pothole.drills import baseline, evaluate, runguard
    from pothole.drills.catalog import Case
    from pothole.llm.client import LLMClient
    from pothole.repair.decide import Repairer

    if split not in ("dev", "test") or system not in ("agent", "baseline"):
        typer.echo("--split is dev or test and --system is agent or baseline", err=True)
        raise typer.Exit(2)
    settings = Settings.from_env()
    tally: Tally | None = None
    make: Callable[[Case], Repairer] = lambda case: baseline.repair  # noqa: E731
    if system == "agent":
        if not (settings.azure_endpoint and settings.azure_api_key):
            typer.echo("the Azure endpoint or key is not set", err=True)
            raise typer.Exit(1)
        client = LLMClient(settings)
        tally = Tally()
        make = lambda case: evaluate.agent_repairer(case, client)  # noqa: E731
    try:
        body = evaluate.run_split(
            split, system, run_id, results, make, tally=tally, deployment=settings.deployment,
            today=date.today(), timeout_s=case_timeout, after_credit=after_credit,
            rerun_test=rerun_test,
        )  # fmt: skip
    except runguard.RefusedToStart as exc:
        typer.echo(f"refusing to run: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(
        f"{body['cases']} cases, cost {body['total_cost']}, repair success {body['repair_success']}"
    )


@app.command("compare")
def compare_command(
    a: Annotated[str, typer.Option("--a", help="Run id, usually the agent.")],
    b: Annotated[str, typer.Option("--b", help="Run id, usually the baseline.")],
    results: Annotated[Path, typer.Option(help="Where run folders are.")] = Path("results"),
) -> None:
    """Run a minus run b, paired by case, with an interval clustered by family."""
    from pothole.drills import compare

    try:
        body = compare.compare(results / a, results / b)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    (results / f"compare-{a}-{b}.json").write_text(json.dumps(body, indent=2) + "\n")
    typer.echo(compare.render(body))


@app.command("eval-summary")
def eval_summary_command(
    run_id: Annotated[str, typer.Option(help="The run folder under results/.")],
    results: Annotated[Path, typer.Option(help="Where run folders are.")] = Path("results"),
) -> None:
    """Rebuild a run's summary.json from its own files; calls no model."""
    from pothole.drills import evaluate

    try:
        body = evaluate.write_summary(results / run_id, None)
    except FileNotFoundError as exc:
        typer.echo(f"no such run: {exc.filename}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(
        f"{body['cases']} cases, cost {body['total_cost']}, per case {body['cost_per_case']} "
        f"over {body['cases_that_called_the_repairer']} that called the repairer"
    )
