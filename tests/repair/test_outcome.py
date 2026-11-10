import json
from datetime import datetime

import pyarrow as pa

from pothole.repair.outcome import RepairOutcome, table_rows


def test_table_rows_are_json_ready_and_keep_microseconds() -> None:
    t = pa.table(
        {
            "unique_key": ["1", "2"],
            "created_date": pa.array(
                [datetime(2026, 11, 3, 8, 30, 12, 123456), None], pa.timestamp("us")
            ),
            "latitude": [40.7, None],
        }
    )
    rows = table_rows(t)
    assert rows[0] == {
        "unique_key": "1",
        "created_date": "2026-11-03T08:30:12.123456",
        "latitude": 40.7,
    }
    assert rows[1]["created_date"] is None
    json.dumps(rows)


def test_outcome_defaults_to_no_ops_and_zero_cost() -> None:
    o = RepairOutcome("no_change", reason="a real spike")
    assert o.ops == [] and o.regression is None and str(o.cost) == "0"
