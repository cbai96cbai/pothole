"""One real model call sequence through the spend tally (a few cents). Run by hand:
`pytest -m live tests/repair/test_live.py`."""

from decimal import Decimal

import pytest

from pothole.budget.tally import Tally
from pothole.config import Settings
from pothole.llm.client import LLMClient
from pothole.repair.agent import repair

MAX_RUN = Decimal("0.10")


@pytest.mark.live
def test_renamed_field_on_a_recorded_day(spec, failing, failed_checks, fields) -> None:
    tally = Tally()
    tally.admit("live-repair-smoke", Decimal("0.048"))  # six calls at the per-call maximum
    outcome = repair(
        spec, failing.rows, failed_checks, fields, LLMClient(Settings.from_env()),
        now=failing.now,
    )  # fmt: skip
    tally.settle("live-repair-smoke", outcome.cost)
    assert outcome.cost <= MAX_RUN
    assert outcome.action == "patch", outcome.reason
    assert [o.model_dump() for o in outcome.ops] == [
        {"op": "map_field", "column": "agency", "source": "agency_name"}
    ]
