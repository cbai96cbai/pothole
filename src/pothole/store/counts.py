"""Daily counts from the raw window, by New York created date."""

from datetime import date

import pyarrow as pa

from pothole.analyst.universe import Universe
from pothole.spec.normalise import board
from pothole.store.db import RAW, Connection


def daily_totals(con: Connection) -> dict[date, int]:
    rows = con.execute(
        f"SELECT CAST(created_date AS DATE) AS d, count(*) FROM {RAW} "
        "WHERE created_date IS NOT NULL GROUP BY d"
    ).fetchall()
    return {d: int(n) for d, n in rows}


def vintage(con: Connection, run_date: date) -> list[tuple[date, int]]:
    """(created date, rows held now) for every day up to the run date, oldest first."""
    return sorted((d, n) for d, n in daily_totals(con).items() if d <= run_date)


def regenerate(con: Connection, universe: Universe | None = None) -> pa.Table:
    """Rows per created date, borough, district number and complaint type. Rows whose community
    board is outside the 59 districts are left out, as are those outside `universe` when given."""
    rows = con.execute(
        f"SELECT CAST(created_date AS DATE), community_board, complaint_type, count(*) "
        f"FROM {RAW} WHERE created_date IS NOT NULL AND complaint_type IS NOT NULL "
        "GROUP BY ALL"
    ).fetchall()
    totals: dict[tuple[date, str, int, str], int] = {}
    for day, raw_board, ctype, n in rows:
        district = board(raw_board)
        if district is None:
            continue
        if universe is not None and (
            ctype not in universe.complaint_types or district not in universe.boards
        ):
            continue
        key = (day, district[0], district[1], ctype)
        totals[key] = totals.get(key, 0) + int(n)
    ordered = sorted(totals.items())
    return pa.table(
        {
            "date": pa.array([k[0] for k, _ in ordered], type=pa.date32()),
            "borough": pa.array([k[1] for k, _ in ordered], type=pa.string()),
            "board": pa.array([k[2] for k, _ in ordered], type=pa.int64()),
            "complaint_type": pa.array([k[3] for k, _ in ordered], type=pa.string()),
            "n": pa.array([n for _, n in ordered], type=pa.int64()),
        }
    )
