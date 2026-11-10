from datetime import datetime
from pathlib import Path

import duckdb

from pothole.analyst import delay


def make_store(rows: list[tuple[datetime, datetime]]) -> duckdb.DuckDBPyConnection:
    """(created_date in New York, _created_at in UTC) pairs."""
    con = duckdb.connect()
    con.execute("CREATE TABLE raw (created_date TIMESTAMP, _created_at TIMESTAMP)")
    if rows:
        con.executemany("INSERT INTO raw VALUES (?, ?)", rows)
    return con


def test_reload_is_the_most_common_arrival_and_only_later_rows_count() -> None:
    reload = datetime(2026, 10, 1, 6, 0)  # UTC; 02:00 in New York
    rows = [(datetime(2026, 9, 1, 10, 0), reload)] * 50  # filed before the reload: not measured
    # filed Oct 5 (10:00 NY): 6 arrive that evening, 3 the next day, 1 two days later
    made = datetime(2026, 10, 5, 10, 0)
    rows += [(made, datetime(2026, 10, 5, 23, 0))] * 6  # UTC 23:00 = 19:00 NY, lag 0
    rows += [(made, datetime(2026, 10, 6, 15, 0))] * 3  # lag 1
    rows += [(made, datetime(2026, 10, 7, 15, 0))] * 1  # lag 2
    # a late-evening NY row: 03:30 UTC on Oct 7 is 23:30 NY on Oct 6, so lag 1
    rows += [(datetime(2026, 10, 5, 23, 50), datetime(2026, 10, 7, 3, 30))] * 2
    # a last day so that all of the above have time to arrive
    rows += [(datetime(2026, 10, 12, 9, 0), datetime(2026, 10, 12, 20, 0))]
    d = delay.measure(make_store(rows))
    assert d.reload_at == datetime(2026, 10, 1, 2, 0)
    # Oct 12 only sets the latest arrival day; a day that recent is too new to measure
    assert d.rows == 12
    assert d.share_by_lag[0] == 6 / 12
    assert d.share_by_lag[1] == 11 / 12
    assert d.share_by_lag[2] == 1.0
    assert d.rows_by_lag == dict.fromkeys(range(7), 12)  # lag 7 needs a day more than 7 old


def test_empty_store_has_no_delay() -> None:
    d = delay.measure(make_store([]))
    assert d.reload_at is None and d.share_by_lag == {}


def test_save_writes_the_shares(tmp_path: Path) -> None:
    path = tmp_path / "delay.json"
    delay.save(
        delay.Delay(datetime(2026, 9, 11, 21, 33), 100, {0: 0.04, 1: 0.9}, {0: 100, 1: 90}), path
    )
    assert '"1": 0.9' in path.read_text()
