import json
from pathlib import Path

import httpx
import respx
from typer.testing import CliRunner

from pothole import cli

URL = "https://data.cityofnewyork.us/resource/erm2-nwe9.json"


@respx.mock
def test_record_batches_writes_six_sorted_files(tmp_path: Path) -> None:
    rows = [{"unique_key": str(i), ":id": "x", ":updated_at": "t"} for i in range(cli.BATCH_ROWS)]
    route = respx.get(URL).mock(return_value=httpx.Response(200, json=rows))
    result = CliRunner().invoke(cli.app, ["record-batches", "--out", str(tmp_path)])
    assert result.exit_code == 0, result.output
    files = sorted(tmp_path.glob("*.json"))
    assert len(files) == 6 and route.call_count == 6
    assert len(json.loads(files[0].read_text())) == cli.BATCH_ROWS
    assert route.calls[0].request.url.params["$order"] == "unique_key"


@respx.mock
def test_record_batches_refuses_an_incomplete_day(tmp_path: Path) -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, json=[{"unique_key": "1"}]))
    result = CliRunner().invoke(cli.app, ["record-batches", "--out", str(tmp_path)])
    assert result.exit_code == 1
    assert not list(tmp_path.glob("*.json"))
