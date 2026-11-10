"""Everything the site shows, from committed files only: run records, analyst days, the
measurement files and the repair scores. `build` writes the JSON files the web app imports."""

import json
import re
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, TypeAdapter

from pothole import runrecord
from pothole import sitemodels as m
from pothole.analyst.finding import AnalystDay, Finding, to_json
from pothole.budget.envelope import (
    CEILING,
    CEILING_DAYS,
    DEFAULT_STEPS,
    MONTHLY_LIMIT,
    run_envelope,
)
from pothole.budget.limits import DEFAULT_LIMITS, PRICE, call_max_cost
from pothole.repair.render import SPEC_PATH
from pothole.runrecord import RunRecord
from pothole.spec.model import PipelineSpec
from pothole.store.load import ANALYSIS_LAG_DAYS

FINDING_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
# raw complaint fields the site never publishes (descriptor fields and coordinates are the
# documented exception)
FORBIDDEN_KEYS = frozenset(
    {
        "resolution_description",
        "incident_address",
        "street_name",
        "cross_street_1",
        "cross_street_2",
        "intersection_street_1",
        "intersection_street_2",
        "location",
        "agency_name",
        "descriptor_free_text",
    }
)
# the file each site model is written to, and the schema name it is validated against
FILES: dict[str, type[BaseModel]] = {
    "board": m.Board,
    "incidents": m.Incidents,
    "drills": m.Drills,
    "findings_index": m.FindingsIndex,
    "repairs": m.Repairs,
    "backtest": m.Backtest,
    "delay": m.Delay,
    "null_simulation": m.NullSimulation,
    "runs": m.Runs,
    "meta": m.Meta,
}


def _strict(schema: Any) -> None:
    """Every field of an object that lists its fields is required and no other is allowed: the
    dataclasses' defaults are for the writers; a file the pages read must hold every field."""
    if isinstance(schema, dict):
        props = schema.get("properties")
        if schema.get("type") == "object" and props is not None:
            schema["required"] = sorted(props)
            schema["additionalProperties"] = False
        for v in schema.values():
            _strict(v)
    elif isinstance(schema, list):
        for v in schema:
            _strict(v)


def _facts(root: Path) -> m.Facts:
    spec = PipelineSpec.from_yaml((root / SPEC_PATH).read_text())
    universe = json.loads((root / "data" / "universe.json").read_text())
    return m.Facts(
        window_days=spec.source.window_days,
        analysis_lag_days=ANALYSIS_LAG_DAYS,
        complaint_types=len(universe["complaint_types"]),
        districts=len(universe["boards"]),
        max_investigations=sum(s.name.startswith("investigation") for s in DEFAULT_STEPS),
        calls_per_run=sum(s.calls for s in DEFAULT_STEPS),
        max_cost_per_call=str(call_max_cost(DEFAULT_LIMITS)),
        run_envelope=str(run_envelope()),
        run_ceiling=str(CEILING),
        ceiling_days=CEILING_DAYS,
        monthly_limit=str(MONTHLY_LIMIT),
        price_input_per_million=str(PRICE.input),
        price_output_per_million=str(PRICE.output),
    )


def schemas() -> dict[str, dict[str, Any]]:
    out = {name: model.model_json_schema(mode="serialization") for name, model in FILES.items()}
    out["finding"] = TypeAdapter(Finding).json_schema(mode="serialization")
    for schema in out.values():
        _strict(schema)
    return out


def write_schemas(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, schema in schemas().items():
        (out_dir / f"{name}.schema.json").write_text(json.dumps(schema, indent=2) + "\n")


def _summary(r: RunRecord) -> m.RunSummary:
    return m.RunSummary(
        run_id=r.run_id,
        started=r.started,
        finished=r.finished,
        status=r.status,
        rows_loaded=r.rows_loaded,
        pages=r.pages,
        analyst_day=r.analyst_day,
        ai_cost=r.ai_cost,
        generation=r.generation,
    )


def _board(runs: list[RunRecord], days: list[AnalystDay]) -> m.Board:
    last = runs[-1] if runs else None
    success = next((r for r in reversed(runs) if r.status == "success"), None)
    checks: dict[str, m.LastCheck] = {}
    for r in runs:  # later runs overwrite earlier ones: each check's last result
        for c in r.checks:
            checks[c.name] = m.LastCheck(
                name=c.name, level=c.level, detail=c.detail, run_id=r.run_id, finished=r.finished
            )
    month = last.finished[:7] if last else None
    cost = sum((Decimal(r.ai_cost) for r in runs if month and r.finished[:7] == month), Decimal(0))
    latest = max(days, key=lambda d: d.day, default=None)
    return m.Board(
        dataset_status=last.status if last else "no_runs",
        last_run=_summary(last) if last else None,
        last_success=_summary(success) if success else None,
        checks=sorted(checks.values(), key=lambda c: c.name),
        latest_day=m.LatestDay(
            day=latest.day.isoformat(),
            tested=latest.tested,
            cleared=latest.cleared,
            findings=len(latest.findings),
            skipped_reason=latest.skipped_reason,
        )
        if latest
        else None,
        month=month,
        month_ai_cost=str(cost),
    )


def _items(runs: list[RunRecord], drill: bool) -> list[m.IncidentItem]:
    return [
        m.IncidentItem(
            run_id=r.run_id,
            started=r.started,
            status=r.status,
            repair=m.Repair(**to_json(r.repair)),
            failed_checks=[m.Check(**to_json(c)) for c in r.checks if c.level == "fail"],
            ai_cost=r.ai_cost,
        )
        for r in reversed(runs)
        if r.repair is not None and r.repair.drill == drill
    ]


def _load_days(root: Path) -> list[AnalystDay]:
    adapter = TypeAdapter(AnalystDay)
    files = sorted((root / "data" / "analyst").glob("*.json"))
    return [adapter.validate_python(json.loads(f.read_text())) for f in files]


def _repairs(root: Path) -> m.Repairs:
    rows = [
        m.RepairSummary.model_validate_json(f.read_text())
        for f in sorted((root / "results").glob("*/summary.json"))
    ]
    rows.sort(key=lambda s: (s.repairer != "baseline", s.repairer, s.split))
    return m.Repairs(summaries=rows, agent_run=any(s.repairer != "baseline" for s in rows))


def _findings(days: list[AnalystDay]) -> tuple[m.FindingsIndex, dict[str, Finding]]:
    by_id: dict[str, Finding] = {}
    cards = []
    for d in sorted(days, key=lambda d: d.day, reverse=True):
        day_cards = []
        for f in d.findings:
            if not FINDING_ID.match(f.id):
                raise ValueError(f"finding id is not a safe file name: {f.id!r}")
            if f.id in by_id:
                raise ValueError(f"two findings share the id {f.id!r}")
            by_id[f.id] = f
            day_cards.append(
                m.FindingCard(**{k: getattr(f, k) for k in m.FindingCard.model_fields})
            )
        cards.append(
            m.FindingDay(
                day=d.day.isoformat(),
                tested=d.tested,
                cleared=d.cleared,
                skipped_reason=d.skipped_reason,
                findings=day_cards,
            )
        )
    return m.FindingsIndex(days=cards), by_id


def _forbidden(value: Any, where: str) -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            if k in FORBIDDEN_KEYS:
                raise ValueError(f"{where}: field {k!r} is raw complaint data and is not published")
            _forbidden(v, where)
    elif isinstance(value, list):
        for v in value:
            _forbidden(v, where)


def _write(path: Path, payload: Any) -> None:
    _forbidden(payload, str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n")


def build(
    repo_root: Path, out_dir: Path, now: datetime | None = None, pending: RunRecord | None = None
) -> None:
    """Write the site data. `pending` is a run's record not yet appended to the run file: the
    site shows it as the newest run, so the record can be appended once the build has worked."""
    runs = runrecord.read(repo_root / "data" / "runs.jsonl") + ([pending] if pending else [])
    days = _load_days(repo_root)
    index, findings = _findings(days)
    board = _board(runs, days)
    data = repo_root / "data"
    payloads: dict[str, BaseModel] = {
        "board": board,
        "incidents": m.Incidents(incidents=_items(runs, drill=False)),
        "drills": m.Drills(drills=_items(runs, drill=True)),
        "findings_index": index,
        "repairs": _repairs(repo_root),
        "backtest": m.Backtest.model_validate_json((data / "backtest.json").read_text()),
        "delay": m.Delay.model_validate_json((data / "delay.json").read_text()),
        "null_simulation": m.NullSimulation.model_validate_json(
            (data / "null_simulation.json").read_text()
        ),
        "runs": m.Runs(runs=[_summary(r) for r in reversed(runs)]),
        "meta": m.Meta(
            facts=_facts(repo_root),
            generated=(now or datetime.now(UTC)).isoformat(timespec="seconds"),
            last_success_finished=board.last_success.finished if board.last_success else None,
            last_success_run_id=board.last_success.run_id if board.last_success else None,
        ),
    }
    names = {"findings_index": "findings/index"}
    for name, model in payloads.items():
        _write(out_dir / f"{names.get(name, name)}.json", m.as_dict(model))
    for fid, f in findings.items():
        _write(out_dir / "findings" / f"{fid}.json", to_json(f))


if __name__ == "__main__":
    root = Path(sys.argv[1])
    write_schemas(root / "web" / "schema")
    build(root, root / "web" / "src" / "data")
