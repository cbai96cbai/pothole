from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from pothole.budget.limits import CallLimits
from pothole.llm.client import CallResult, Usage
from pothole.repair.validate import Batch, load_batches
from pothole.spec import engine
from pothole.spec.checks import CheckResult
from pothole.spec.model import PipelineSpec

ROOT = Path(__file__).parents[2]
FAILING_DAY = "2026-11-07.jsonl.gz"


@pytest.fixture(scope="session")
def spec() -> PipelineSpec:
    return PipelineSpec.load(ROOT / "pipelines" / "311.yaml")


@pytest.fixture(scope="session")
def all_batches() -> list[Batch]:
    return load_batches(ROOT / "fixtures" / "batches")


@pytest.fixture(scope="session")
def failing(all_batches: list[Batch]) -> Batch:
    """A real recorded day in which the source renamed `agency` to `agency_name`."""
    day = next(b for b in all_batches if b.name == FAILING_DAY)
    rows = [{("agency_name" if k == "agency" else k): v for k, v in r.items()} for r in day.rows]
    return Batch(day.name, rows, day.now)


@pytest.fixture(scope="session")
def failed_checks(spec: PipelineSpec, failing: Batch) -> list[CheckResult]:
    return engine.run(spec, failing.rows, [], failing.now).checks


@pytest.fixture(scope="session")
def fields(failing: Batch) -> dict[str, str]:
    return {k: "text" for k in failing.rows[0]}


def call(name: str, args: dict[str, Any], id_: str = "c1") -> dict[str, Any]:
    import json

    return {
        "id": id_,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


def reply(*calls: dict[str, Any], content: str | None = None) -> CallResult:
    message: dict[str, Any] = {"role": "assistant", "content": content, "tool_calls": list(calls)}
    usage = Usage(1000, 100, Decimal("0.001"))
    return CallResult(message, usage, "tool_calls" if calls else "stop", 0.0)


class Scripted:
    """A model that answers from a list; records what each call was sent."""

    def __init__(self, script: list[CallResult | Exception | Callable[[], CallResult]]) -> None:
        self.script = list(script)
        self.sent: list[dict[str, Any]] = []

    def call(
        self,
        messages: Any,
        tools: Any = None,
        limits: CallLimits | None = None,
        tool_choice: Any = None,
    ) -> CallResult:
        self.sent.append({"messages": [dict(m) for m in messages], "tool_choice": tool_choice})
        nxt = self.script.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt() if callable(nxt) else nxt


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 11, 8, 6, 0)


@pytest.fixture
def outcome_case(spec: PipelineSpec, failing: Batch) -> Any:
    from pothole.repair.outcome import RegressionCase, table_rows

    rows = failing.rows[:3]
    return RegressionCase(rows, table_rows(engine.run(spec, rows, [], failing.now).table))
