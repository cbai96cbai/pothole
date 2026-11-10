from datetime import date, timedelta
from pathlib import Path

import httpx
import respx
from typer.testing import CliRunner

from pothole import cli
from pothole.socrata.batches import dump, load

URL = "https://data.cityofnewyork.us/resource/erm2-nwe9.json"


@respx.mock
def test_record_batches_writes_six_sorted_files(tmp_path: Path) -> None:
    rows = [{"unique_key": str(i), ":id": "x", ":updated_at": "t"} for i in range(cli.BATCH_ROWS)]
    route = respx.get(URL).mock(return_value=httpx.Response(200, json=rows))
    result = CliRunner().invoke(cli.app, ["record-batches", "--out", str(tmp_path)])
    assert result.exit_code == 0, result.output
    files = sorted(tmp_path.glob("*.jsonl.gz"))
    assert len(files) == 6 and route.call_count == 6
    assert load(files[0]) == rows
    days = [f.name.removesuffix(".jsonl.gz") for f in files]
    assert len(set(days)) == 6
    params = [c.request.url.params for c in route.calls]
    assert {p["$select"] for p in params} == {"*, :id, :updated_at"}
    assert all(p["$order"] == "unique_key" and p["$limit"] == "2000" for p in params)
    for day, p in zip(days, sorted(params, key=lambda q: q["$where"]), strict=True):
        nxt = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
        assert (
            p["$where"] == f"created_date >= '{day}T00:00:00' AND created_date < '{nxt}T00:00:00'"
        )


def test_dump_is_deterministic_and_loads_back(tmp_path: Path) -> None:
    rows = [{"b": "2", "a": "1"}, {"c": "3"}]
    one, two = tmp_path / "one.jsonl.gz", tmp_path / "two.jsonl.gz"
    dump(one, rows)
    dump(two, rows)
    assert one.read_bytes() == two.read_bytes()
    assert load(one) == rows


@respx.mock
def test_record_batches_refuses_an_incomplete_day(tmp_path: Path) -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, json=[{"unique_key": "1"}]))
    result = CliRunner().invoke(cli.app, ["record-batches", "--out", str(tmp_path)])
    assert result.exit_code == 1
    assert not list(tmp_path.glob("*.jsonl.gz"))
