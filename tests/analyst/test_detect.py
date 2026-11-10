from datetime import date, timedelta

import numpy as np
import pyarrow as pa

from pothole.analyst import detect
from pothole.analyst.detect import Panel
from pothole.analyst.holidays import HOLIDAYS
from pothole.analyst.universe import Universe

UNIVERSE = Universe(frozenset({"Noise", "Heat"}), frozenset({("Queens", 3), ("Bronx", 1)}))
START = date(2026, 8, 3)  # a Monday
SERIES = [
    ("Queens", 3, "Noise"),
    ("Queens", 3, "Heat"),
    ("Bronx", 1, "Noise"),
    ("Bronx", 1, "Heat"),
]


def table(values: dict[tuple[str, int, str], dict[date, int]]) -> pa.Table:
    rows = [(d, *k, n) for k, by_day in values.items() for d, n in by_day.items()]
    cols = list(zip(*rows, strict=True))
    return pa.table(
        {
            "date": pa.array(cols[0], pa.date32()),
            "borough": list(cols[1]),
            "board": pa.array(cols[2], pa.int64()),
            "complaint_type": list(cols[3]),
            "n": pa.array(cols[4], pa.int64()),
        }
    )


def steady(days: int = 70, level: int = 20, spike: dict[date, int] | None = None) -> pa.Table:
    """Four series at `level` every day, wobbling by 1; `spike` sets the Queens 3 Noise count."""
    out: dict[tuple[str, int, str], dict[date, int]] = {}
    for s, key in enumerate(SERIES):
        out[key] = {START + timedelta(d): level + (d + s) % 3 - 1 for d in range(days)}
    for d, n in (spike or {}).items():
        out[SERIES[0]][d] = n
    return table(out)


def test_quiet_day_spends_nothing() -> None:
    t = steady()
    for offset in range(60, 70):
        assert detect.candidates(t, START + timedelta(offset), UNIVERSE) == []


def test_planted_spike_found() -> None:
    day = START + timedelta(66)
    found = detect.candidates(steady(spike={day: 90}), day, UNIVERSE)
    assert [(c.borough, c.board, c.complaint_type) for c in found] == [("Queens", 3, "Noise")]
    c = found[0]
    assert c.observed == 90 and c.ratio > 4 and c.q <= 0.05 and c.day == day
    assert 19 < c.expected < 21


def test_holiday_skipped_and_excluded() -> None:
    labor = date(2026, 9, 7)  # a Monday and a federal holiday
    assert labor in HOLIDAYS
    t = steady(days=70, spike={labor: 200})
    assert detect.candidates(t, labor, UNIVERSE) == []  # a holiday is not analysed
    panel = Panel.from_table(t, UNIVERSE)
    # Sep 28 has exactly eight Mondays before it in the window, one of them Labor Day: only 7 left
    assert detect.baseline_columns(panel, panel.index(date(2026, 9, 28))) is None
    cols = detect.baseline_columns(panel, panel.index(date(2026, 10, 5)))  # nine Mondays before
    assert cols is not None and len(cols) == 8 and panel.index(labor) not in cols


def test_short_window_is_not_tested() -> None:
    t = steady(days=56)  # the 57th day would need 8 Mondays inside a 56-day window
    assert detect.candidates(t, START + timedelta(56), UNIVERSE) == []
    panel = Panel.from_table(t, UNIVERSE)
    assert detect.evaluate(panel, START + timedelta(55)) is None
    assert detect.evaluate(panel, START + timedelta(56)) is None


def test_predictive_variance_never_below_poisson() -> None:
    mean = np.array([0.1, 1.0, 5.0, 40.0, 300.0])
    for k in (0.5, 1.0, 10.0, 1e3, 1e6):
        r, p = detect.predictive_params(mean, np.full(5, k))
        assert np.allclose(r * (1 - p) / p, mean)  # the predictive mean is the baseline mean
        variance = r * (1 - p) / p**2
        assert (variance > mean).all()
