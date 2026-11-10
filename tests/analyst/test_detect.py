import math
import time
from datetime import date, timedelta

import numpy as np
import pyarrow as pa
import pytest
from scipy.stats import poisson

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


def test_predictive_never_narrower_than_poisson() -> None:
    rng = np.random.default_rng(7)
    for _ in range(300):
        base = rng.poisson(rng.uniform(1, 60), size=(1, 8)).astype(float)
        mean = base.mean()
        if mean == 0 or base.var(ddof=1) > mean:  # dispersion 1: the model is a Gamma-Poisson
            continue
        for x in (mean * 1.5, mean * 2.5, mean * 4):
            obs = np.array([math.ceil(x)], dtype=float)
            ours = detect.predictive_tail(base, obs)[0]
            assert ours >= poisson.sf(obs[0] - 1, mean) - 1e-12


def _any_flag_rate(matrix: np.ndarray) -> float:  # type: ignore[type-arg]
    series, columns = matrix.shape
    panel = Panel(START, [("Queens", i, "x") for i in range(series)], matrix.astype(np.int64))
    flagged = tested = 0
    for i in range(63, columns):
        ev = detect.evaluate(panel, panel.day_at(i))
        if ev is not None:
            tested += 1
            flagged += bool((ev.q <= detect.FDR).any())
    assert tested >= 190
    return flagged / tested


BOUND = 0.05 + 3 * math.sqrt(0.05 * 0.95 / 200)  # 0.096


def test_null_simulation_any_flag_rate() -> None:
    """Under the global null, BH keeps P(any flag on a day) at or below q: 1,475 series x 200 days
    of Poisson counts."""
    rng = np.random.default_rng(7)
    means = rng.lognormal(mean=2.2, sigma=0.9, size=1475)
    started = time.monotonic()
    rate = _any_flag_rate(rng.poisson(means[:, None], size=(1475, 263)))
    assert rate <= BOUND < 0.0966
    assert time.monotonic() - started < 20


@pytest.mark.xfail(strict=True, reason="the quasi-Poisson predictive is too narrow in its tail")
def test_null_simulation_overdispersed_any_flag_rate() -> None:
    """The same null with counts 1.5 times more spread than Poisson: 0.47 of days flag."""
    rng = np.random.default_rng(7)
    means = rng.lognormal(mean=2.2, sigma=0.9, size=1475)
    size = means / 0.5  # negative binomial: variance = 1.5 x mean
    counts = rng.negative_binomial(size[:, None], (size / (size + means))[:, None], (1475, 263))
    assert _any_flag_rate(counts) <= BOUND
