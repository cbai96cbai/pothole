from datetime import date, timedelta

import numpy as np
import pyarrow as pa

from pothole.analyst import detect
from pothole.analyst.detect import Panel
from pothole.analyst.holidays import HOLIDAYS
from pothole.analyst.universe import Universe
from pothole.store.coverage import Coverage

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


def test_dispersion_pools_by_type_and_clamps() -> None:
    types = np.array([0, 0, 1, 1, 2])
    mean = np.array([10.0, 20.0, 5.0, 5.0, 8.0])
    var = np.array([20.0, 60.0, 5.0, 4.0, 8.5])
    k = detect.type_dispersion(mean, var, types)
    assert k[0] == (100 + 400) / (10 + 40)  # pooled over both series of type 0
    assert k[1] == detect.K_RANGE[1]  # no series above Poisson: zero excess, Poisson
    assert k[2] == 64 / 0.5  # a little excess: k is large but inside the range
    big = detect.type_dispersion(np.array([100.0]), np.array([100.001]), np.array([0]))
    assert big[0] == detect.K_RANGE[1]  # 1e4 / 0.001 clamps at the top
    low = detect.type_dispersion(np.array([1.0]), np.array([100.0]), np.array([0]))
    assert low[0] == detect.K_RANGE[0]  # 1 / 99 clamps at the bottom


def test_today_enters_neither_baseline_nor_dispersion() -> None:
    day = START + timedelta(66)
    quiet = Panel.from_table(steady(), UNIVERSE)
    loud = Panel.from_table(steady(spike={day: 5000}), UNIVERSE)
    a, b = detect.evaluate(quiet, day), detect.evaluate(loud, day)
    assert a is not None and b is not None
    assert np.array_equal(a.expected, b.expected)
    others = [i for i, s in enumerate(a.series) if quiet.keys[int(s)] != SERIES[0]]
    assert np.array_equal(a.p[others], b.p[others])  # the type's k did not move


def test_the_panel_spans_the_days_the_store_covers_not_the_days_with_counts() -> None:
    t = steady(days=60)  # counts on START .. START + 59
    held = Coverage(START - timedelta(2), START + timedelta(61))
    panel = Panel.from_table(t, UNIVERSE, held)
    assert panel.start == held.start and panel.days == 64
    assert panel.matrix[:, :2].sum() == 0 and panel.matrix[:, -2:].sum() == 0  # real zeros
    without = Panel.from_table(t, UNIVERSE)
    assert without.start == START and without.days == 60


def test_counts_outside_the_covered_span_are_left_out() -> None:
    held = Coverage(START + timedelta(5), START + timedelta(30))
    panel = Panel.from_table(steady(days=60), UNIVERSE, held)
    assert panel.days == 26 and panel.start == held.start


def test_the_daily_screen_tests_a_day_the_covered_span_makes_testable() -> None:
    from pothole import daily

    # counts only from START + 14; the store covers from START, so eight Mondays precede Oct 5
    t = table({k: {START + timedelta(d): 20 + d % 3 for d in range(14, 70)} for k in SERIES})
    day = START + timedelta(63)
    skipped = daily.screen(t, UNIVERSE, day)
    assert getattr(skipped, "skipped_reason", "") == "no 8-week baseline for this day"
    tested = daily.screen(t, UNIVERSE, day, Coverage(START, START + timedelta(70)))
    assert isinstance(tested, list) and tested[0] == 4
