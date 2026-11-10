"""What a repairer hands back: the contract shared by the repair agent, the non-AI baseline and
the judge."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

import pyarrow as pa

from pothole.spec.ops import AddValueMapping, MapField, SetFormat

Action = Literal["patch", "issue", "no_change"]
Op = MapField | SetFormat | AddValueMapping


@dataclass(frozen=True)
class RegressionCase:
    """Input rows as the source sends them, and the engine's expected output (see `table_rows`)."""

    rows: list[dict[str, str]]
    expected: list[dict[str, Any]]


@dataclass(frozen=True)
class RepairOutcome:
    action: Action
    ops: list[Op] = field(default_factory=list)
    regression: RegressionCase | None = None
    reason: str = ""
    trace: list[dict[str, Any]] = field(default_factory=list)
    cost: Decimal = Decimal("0")


def table_rows(table: pa.Table) -> list[dict[str, Any]]:
    """Engine output as JSON-ready rows: timestamps as ISO strings, other values unchanged."""
    return [
        {k: v.isoformat() if isinstance(v, datetime) else v for k, v in row.items()}
        for row in table.to_pylist()
    ]
