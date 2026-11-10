"""Real investigations of recent days through the spend tally (at most $0.30 in all). Run by
hand against a loaded store: `POTHOLE_DB=<file> pytest -m live tests/analyst`."""

import os
from decimal import Decimal
from pathlib import Path

import pytest

from pothole.analyst import investigate
from pothole.analyst.detect import Candidate
from pothole.analyst.wording import causal_phrase
from pothole.budget.tally import Tally
from pothole.config import Settings
from pothole.llm.client import LLMClient

BUDGET = Decimal("0.30")


@pytest.mark.live
def test_three_recent_days() -> None:
    db = os.environ.get("POTHOLE_DB")
    if not db or not Path(db).exists():
        pytest.skip("set POTHOLE_DB to a loaded store")
    import duckdb

    con = duckdb.connect(db, read_only=True)
    rows = con.execute(
        "SELECT CAST(created_date AS DATE) d, community_board, complaint_type, count(*) n "
        "FROM raw WHERE community_board LIKE '% BROOKLYN' GROUP BY ALL ORDER BY d DESC, n DESC "
        "LIMIT 3"
    ).fetchall()
    con.close()
    tally, client, spent = Tally(), LLMClient(Settings.from_env()), Decimal(0)
    tally.admit("live-investigate", Decimal("0.24"))  # three runs of five calls at the maximum
    for day, raw, ctype, n in rows:
        c = Candidate("Brooklyn", int(raw[:2]), ctype, day, int(n), float(n) / 2, 2.0, 0.0, 0.0)
        finding = investigate.run(c, Path(db), client)
        spent += finding.cost
        assert finding.summary and not causal_phrase(finding.summary)
    tally.settle("live-investigate", spent)
    assert spent <= BUDGET
