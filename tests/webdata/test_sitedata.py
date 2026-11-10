import json
from pathlib import Path
from typing import Any

import pytest
import site_repo

from pothole import sitedata

WEB = Path(__file__).resolve().parents[2] / "web"


def check(value: Any, schema: dict[str, Any], defs: dict[str, Any], path: str = "$") -> None:
    """Enough of JSON Schema for the schemas pydantic writes."""
    if "$ref" in schema:
        return check(value, defs[schema["$ref"].split("/")[-1]], defs, path)
    if "anyOf" in schema:
        errs = []
        for option in schema["anyOf"]:
            try:
                return check(value, option, defs, path)
            except AssertionError as e:
                errs.append(str(e))
        raise AssertionError(f"{path}: no option matched: {errs}")
    if "const" in schema:
        assert value == schema["const"], path
    if "enum" in schema:
        assert value in schema["enum"], f"{path}: {value!r}"
    kind = schema.get("type")
    kinds = {
        "null": lambda v: v is None,
        "string": lambda v: isinstance(v, str),
        "boolean": lambda v: isinstance(v, bool),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "number": lambda v: isinstance(v, int | float) and not isinstance(v, bool),
        "array": lambda v: isinstance(v, list),
        "object": lambda v: isinstance(v, dict),
    }
    if kind is not None:
        assert kinds[kind](value), f"{path}: expected {kind}, got {value!r}"
    if schema.get("format") == "date":
        from datetime import date

        date.fromisoformat(value)
    if kind == "object":
        props = schema.get("properties", {})
        for req in schema.get("required", []):
            assert req in value, f"{path}: missing {req}"
        extra = schema.get("additionalProperties", True)
        for k, v in value.items():
            if k in props:
                check(v, props[k], defs, f"{path}.{k}")
            elif isinstance(extra, dict):
                check(v, extra, defs, f"{path}.{k}")
            else:
                assert extra is not False, f"{path}: unexpected field {k}"
    if kind == "array":
        prefix = schema.get("prefixItems")
        if prefix is not None:
            assert len(value) == len(prefix), f"{path}: wrong length"
            for i, (v, s) in enumerate(zip(value, prefix, strict=True)):
                check(v, s, defs, f"{path}[{i}]")
        elif "items" in schema:
            for i, v in enumerate(value):
                check(v, schema["items"], defs, f"{path}[{i}]")


def validate(value: Any, schema: dict[str, Any]) -> None:
    check(value, schema, schema.get("$defs", {}))


@pytest.fixture
def built(tmp_path: Path) -> Path:
    repo = site_repo.make(tmp_path / "repo")
    sitedata.build(repo, tmp_path / "out")
    return tmp_path / "out"


def load(out: Path, name: str) -> Any:
    return json.loads((out / name).read_text())


def test_every_file_matches_its_schema(built: Path) -> None:
    schemas = sitedata.schemas()
    names = {
        "board.json": "board",
        "incidents.json": "incidents",
        "drills.json": "drills",
        "findings/index.json": "findings_index",
        "repairs.json": "repairs",
        "backtest.json": "backtest",
        "delay.json": "delay",
        "null_simulation.json": "null_simulation",
        "runs.json": "runs",
        "meta.json": "meta",
        "findings/2026-11-06-brooklyn-12-heat.json": "finding",
    }
    written = {p.relative_to(built).as_posix() for p in built.rglob("*.json")}
    assert written == set(names)
    for file, schema in names.items():
        validate(load(built, file), schemas[schema])


def test_committed_schemas_are_current(tmp_path: Path) -> None:
    sitedata.write_schemas(tmp_path)
    for f in tmp_path.glob("*.schema.json"):
        assert (WEB / "schema" / f.name).read_text() == f.read_text(), f"regenerate {f.name}"
    assert {p.name for p in (WEB / "schema").glob("*.json")} == {p.name for p in tmp_path.glob("*")}


def test_the_validator_rejects_a_wrong_file(built: Path) -> None:
    board = load(built, "board.json")
    board["checks"][0]["level"] = 3
    with pytest.raises(AssertionError):
        validate(board, sitedata.schemas()["board"])
    finding = load(built, "findings/2026-11-06-brooklyn-12-heat.json")
    finding["series"][0] = ["2026-10-10"]
    with pytest.raises(AssertionError):
        validate(finding, sitedata.schemas()["finding"])


def test_board_incidents_and_drills(built: Path) -> None:
    board = load(built, "board.json")
    assert board["dataset_status"] == "success"
    assert board["last_success"]["run_id"] == "run-3"
    assert {c["name"]: c["level"] for c in board["checks"]} == {
        "borough_values": "error",
        "row_count": "ok",
    }
    assert board["latest_day"]["day"] == "2026-11-07"
    assert board["month_ai_cost"] == "0.063"
    assert board["monthly_limit"] == "4.00"
    incidents = load(built, "incidents.json")["incidents"]
    assert [i["run_id"] for i in incidents] == ["run-2"]
    assert incidents[0]["repair"]["not_judged"] is True
    assert incidents[0]["repair"]["drill"] is False
    assert incidents[0]["failed_checks"][0]["name"] == "borough_values"
    drills = load(built, "drills.json")["drills"]
    assert [d["run_id"] for d in drills] == ["run-3"]
    assert drills[0]["repair"]["drill"] is True
    assert [r["run_id"] for r in load(built, "runs.json")["runs"]] == ["run-3", "run-2", "run-1"]
    meta = load(built, "meta.json")
    assert meta["last_success_run_id"] == "run-3"


def test_findings_feed_keeps_the_quiet_day(built: Path) -> None:
    days = load(built, "findings/index.json")["days"]
    assert [(d["day"], len(d["findings"])) for d in days] == [("2026-11-07", 0), ("2026-11-06", 1)]
    finding = load(built, "findings/2026-11-06-brooklyn-12-heat.json")
    assert len(finding["points"]) == 20
    assert len(finding["series"]) == 21


def test_repairs_have_the_baseline_and_no_agent_yet(built: Path) -> None:
    repairs = load(built, "repairs.json")
    assert repairs["agent_run"] is False
    assert [(s["repairer"], s["split"]) for s in repairs["summaries"]] == [
        ("baseline", "dev"),
        ("baseline", "test"),
    ]


def test_agent_results_appear_when_they_exist(tmp_path: Path) -> None:
    repo = site_repo.make(tmp_path / "repo", with_agent=True)
    sitedata.build(repo, tmp_path / "out")
    repairs = load(tmp_path / "out", "repairs.json")
    assert repairs["agent_run"] is True
    assert repairs["summaries"][-1]["repairer"] == "agent"


def test_raw_complaint_fields_are_refused(tmp_path: Path) -> None:
    repo = site_repo.make(tmp_path / "repo")
    f = repo / "data" / "analyst" / "2026-11-06.json"
    day = json.loads(f.read_text())
    day["findings"][0]["trace"] = [{"rows": [{"resolution_description": "text"}]}]
    f.write_text(json.dumps(day))
    with pytest.raises(ValueError, match="resolution_description"):
        sitedata.build(repo, tmp_path / "out")


def test_unsafe_finding_ids_are_refused(tmp_path: Path) -> None:
    repo = site_repo.make(tmp_path / "repo")
    f = repo / "data" / "analyst" / "2026-11-06.json"
    day = json.loads(f.read_text())
    day["findings"][0]["id"] = "../escape"
    f.write_text(json.dumps(day))
    with pytest.raises(ValueError, match="safe file name"):
        sitedata.build(repo, tmp_path / "out")


def test_an_empty_history_still_builds(tmp_path: Path) -> None:
    repo = site_repo.make(tmp_path / "repo")
    (repo / "data" / "runs.jsonl").unlink()
    sitedata.build(repo, tmp_path / "out")
    board = load(tmp_path / "out", "board.json")
    assert board["dataset_status"] == "no_runs"
    assert board["last_success"] is None


def test_run_records_round_trip_and_reject_bad_status(tmp_path: Path) -> None:
    from pothole import runrecord

    records = runrecord.read(site_repo.make(tmp_path / "r") / "data" / "runs.jsonl")
    assert len(records) == 3 and records[1].repair is not None
    with pytest.raises(ValueError, match="status"):
        site_repo._run(9, 9, status="fine")
