"""The detector: which district-and-complaint-type counts are unusually high on one day.

Per series, the previous 8 same weekdays inside the window (federal holidays left out) give a
baseline y1..y8. Dispersion phi = max(1, sample variance / mean) rescales counts to y/phi; the
rate's posterior is Gamma(sum(y/phi) + 0.5, 8), so the predictive for observed/phi is negative
binomial with r = shape and p = 8/9, and it carries the baseline's own uncertainty. The p-value is
P(X >= observed/phi). Benjamini-Hochberg at q = 0.05 runs over every series tested that day; a
candidate also needs observed >= 10 and observed/expected >= 2. A series without 8 baseline days
in the window, or with a baseline mean of 0, is not tested.
"""

import json
import statistics
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pyarrow as pa
from numpy.typing import NDArray
from scipy.stats import nbinom

from pothole.analyst.holidays import is_holiday
from pothole.analyst.universe import Universe
from pothole.store import counts
from pothole.store.db import Connection
from pothole.store.load import ANALYSIS_LAG_DAYS

BASELINE_DAYS = 8
FDR = 0.05
MIN_OBSERVED = 10
MIN_RATIO = 2.0
SENT_ON = 2  # the investigator takes this many, largest ratio first
POSTERIOR_RATE = float(BASELINE_DAYS)
PREDICTIVE_P = POSTERIOR_RATE / (POSTERIOR_RATE + 1)
CALIBRATION_PATH = Path(__file__).parents[3] / "data" / "calibration.json"

Key = tuple[str, int, str]  # borough, district number, complaint type
Floats = NDArray[np.float64]


@dataclass(frozen=True)
class Candidate:
    borough: str
    board: int
    complaint_type: str
    day: date
    observed: int
    expected: float
    ratio: float
    p: float
    q: float


@dataclass(frozen=True)
class Panel:
    """Counts as a matrix: one row per series of the universe, one column per day of the window."""

    start: date
    keys: list[Key]
    matrix: NDArray[np.int64]

    @property
    def days(self) -> int:
        return int(self.matrix.shape[1])

    def day_at(self, i: int) -> date:
        return self.start + timedelta(days=i)

    def index(self, day: date) -> int:
        return (day - self.start).days

    @classmethod
    def from_table(cls, table: pa.Table, universe: Universe) -> "Panel":
        keys = sorted((b, n, t) for b, n in universe.boards for t in universe.complaint_types)
        rows = table.to_pylist()
        if not rows:
            raise ValueError("no counts to test")
        first = min(r["date"] for r in rows)
        last = max(r["date"] for r in rows)
        matrix = np.zeros((len(keys), (last - first).days + 1), dtype=np.int64)
        where = {k: i for i, k in enumerate(keys)}
        for r in rows:
            i = where.get((r["borough"], r["board"], r["complaint_type"]))
            if i is not None:
                matrix[i, (r["date"] - first).days] += r["n"]
        return cls(first, keys, matrix)


@dataclass(frozen=True)
class Evaluation:
    """Every series tested on one day; arrays line up with `series`."""

    series: NDArray[np.intp]
    observed: NDArray[np.int64]
    expected: Floats
    p: Floats
    q: Floats


def baseline_columns(panel: Panel, i: int) -> list[int] | None:
    """The 8 previous same weekdays before column i inside the window, holidays skipped."""
    found: list[int] = []
    j = i - 7
    while j >= 0 and len(found) < BASELINE_DAYS:
        if not is_holiday(panel.day_at(j)):
            found.append(j)
        j -= 7
    return found if len(found) == BASELINE_DAYS else None


def predictive_tail(baseline: Floats, observed: Floats) -> Floats:
    """P(X >= observed/phi) for each row of `baseline` (rows x 8) against its own observed."""
    mean = baseline.mean(axis=1)
    phi = np.maximum(1.0, baseline.var(axis=1, ddof=1) / mean)
    shape = (baseline / phi[:, None]).sum(axis=1) + 0.5
    x = observed / phi
    return np.asarray(nbinom.sf(np.ceil(x) - 1, shape, PREDICTIVE_P), dtype=np.float64)


def benjamini_hochberg(p: Floats) -> Floats:
    """BH-adjusted p-values (q-values)."""
    m = len(p)
    order = np.argsort(p)
    adjusted = p[order] * m / np.arange(1, m + 1)
    adjusted = np.minimum.accumulate(adjusted[::-1])[::-1]
    q = np.empty(m)
    q[order] = np.minimum(adjusted, 1.0)
    return q


def evaluate(panel: Panel, day: date) -> Evaluation | None:
    """Test every series on `day`; None when the day is a holiday or has no 8-day baseline."""
    i = panel.index(day)
    if not 0 <= i < panel.days or is_holiday(day):
        return None
    columns = baseline_columns(panel, i)
    if columns is None:
        return None
    baseline = panel.matrix[:, columns].astype(np.float64)
    usable = np.flatnonzero(baseline.mean(axis=1) > 0)
    if len(usable) == 0:
        return None
    observed = panel.matrix[usable, i]
    p = predictive_tail(baseline[usable], observed.astype(np.float64))
    return Evaluation(usable, observed, baseline[usable].mean(axis=1), p, benjamini_hochberg(p))


def _flagged(panel: Panel, ev: Evaluation, day: date) -> list[Candidate]:
    keep = (ev.q <= FDR) & (ev.observed >= MIN_OBSERVED) & (ev.observed / ev.expected >= MIN_RATIO)
    out = []
    for k in np.flatnonzero(keep):
        borough, board, ctype = panel.keys[int(ev.series[k])]
        obs, exp = int(ev.observed[k]), float(ev.expected[k])
        out.append(
            Candidate(
                borough, board, ctype, day, obs, exp, obs / exp, float(ev.p[k]), float(ev.q[k])
            )
        )
    return sorted(out, key=lambda c: (-c.ratio, c.borough, c.board, c.complaint_type))


def candidates(table: pa.Table, day: date, universe: Universe) -> list[Candidate]:
    """Everything that clears the bar on `day`, largest ratio first; the caller takes SENT_ON."""
    panel = Panel.from_table(table, universe)
    ev = evaluate(panel, day)
    return [] if ev is None else _flagged(panel, ev, day)


def eligible_days(panel: Panel) -> list[date]:
    last = panel.days - 1 - ANALYSIS_LAG_DAYS
    return [
        d
        for i in range(last + 1)
        if not is_holiday(d := panel.day_at(i)) and baseline_columns(panel, i) is not None
    ]


def backtest(con: Connection, universe: Universe) -> dict[str, object]:
    """Run the detector on every eligible day of the stored window."""
    panel = Panel.from_table(counts.regenerate(con, universe), universe)
    days = []
    for day in eligible_days(panel):
        ev = evaluate(panel, day)
        flagged = [] if ev is None else _flagged(panel, ev, day)
        days.append(
            {
                "day": day.isoformat(),
                "series_tested": 0 if ev is None else len(ev.series),
                "cleared_the_bar": len(flagged),
                "top": [_row(c) for c in flagged[:SENT_ON]],
            }
        )
    flags = [int(d["cleared_the_bar"]) for d in days]  # type: ignore[call-overload]
    return {
        "label": "flags on past days, not calibration: no day here is known to be a real event",
        "window": [panel.day_at(0).isoformat(), panel.day_at(panel.days - 1).isoformat()],
        "days_tested": len(days),
        "flags_per_day": {
            "min": min(flags, default=0),
            "median": statistics.median(flags) if flags else 0,
            "max": max(flags, default=0),
        },
        "days": days,
    }


def _row(c: Candidate) -> dict[str, object]:
    return {
        "borough": c.borough,
        "board": c.board,
        "complaint_type": c.complaint_type,
        "observed": c.observed,
        "expected": round(c.expected, 2),
        "ratio": round(c.ratio, 2),
        "q": c.q,
    }


def save_backtest(result: dict[str, object], path: Path = CALIBRATION_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2) + "\n")
