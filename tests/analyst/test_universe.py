import json
from pathlib import Path
from typing import Any

from pothole.analyst import universe
from pothole.analyst.universe import Universe


class StubSocrata:
    def __init__(self) -> None:
        self.asked: list[dict[str, str]] = []

    def query(self, dataset: str, params: Any) -> list[dict[str, Any]]:
        self.asked.append(dict(params))
        return [{"complaint_type": f"Type {i}", "n": str(100 - i)} for i in range(25)]


def test_freeze_asks_once_and_keeps_the_25_types_and_59_districts(tmp_path: Path) -> None:
    stub = StubSocrata()
    path = tmp_path / "universe.json"
    frozen = universe.freeze(stub, path)  # type: ignore[arg-type]
    assert len(stub.asked) == 1
    assert "2026-08-01" in stub.asked[0]["$where"] and stub.asked[0]["$group"] == "complaint_type"
    assert len(frozen.complaint_types) == 25 and len(frozen.boards) == 59
    assert ("Staten Island", 3) in frozen.boards and ("Staten Island", 4) not in frozen.boards
    assert universe.load(path) == frozen
    assert json.loads(path.read_text())["span"] == {"from": "2026-08-01", "before": "2026-11-01"}


def test_committed_universe_is_25_by_59() -> None:
    frozen: Universe = universe.load()
    assert len(frozen.complaint_types) == 25 and len(frozen.boards) == 59
    assert "Unspecified" not in frozen.complaint_types
