"""How often the detector flags something on a day where nothing happened.

Simulated counts with no events: 25 complaint types x 59 districts (1,475 series), 200 test days
after a 63-day lead-in, seed 7. Counts are Poisson, or negative binomial with variance
mean + mean^2 / k where k is shared by the 59 series of a type (varied 0.7-1.3 across types) and
set so a typical series (mean 9) has `spread` times the Poisson variance. The rate reported is the
share of days on which at least one series passes the Benjamini-Hochberg filter. These are
measurements of this screen, not a guarantee about real data.
"""

import json
from datetime import date
from pathlib import Path

import numpy as np

from pothole.analyst import detect
from pothole.analyst.detect import Panel

SEED = 7
START = date(2026, 8, 3)
TYPES = 25
DISTRICTS = 59
LEAD_IN = 63
COLUMNS = 263
SPREADS = (1.0, 1.5, 2.0, 3.0)
PATH = Path(__file__).parents[3] / "data" / "null_simulation.json"


def any_flag_rate(spread: float) -> float:
    rng = np.random.default_rng(SEED)
    series = TYPES * DISTRICTS
    means = rng.lognormal(mean=2.2, sigma=0.9, size=series)
    if spread == 1.0:
        matrix = rng.poisson(means[:, None], size=(series, COLUMNS))
    else:
        k = np.repeat(rng.uniform(0.7, 1.3, TYPES), DISTRICTS) * np.exp(2.2) / (spread - 1)
        matrix = rng.negative_binomial(k[:, None], (k / (k + means))[:, None], (series, COLUMNS))
    keys = [("Queens", i % DISTRICTS + 1, f"type {i // DISTRICTS}") for i in range(series)]
    panel = Panel(START, keys, matrix.astype(np.int64))
    flagged = tested = 0
    for i in range(LEAD_IN, COLUMNS):
        ev = detect.evaluate(panel, panel.day_at(i))
        if ev is not None:
            tested += 1
            flagged += bool((ev.q <= detect.FDR).any())
    return flagged / tested


def run(path: Path = PATH) -> dict[str, object]:
    rates = {f"{s:g}x": round(any_flag_rate(s), 4) for s in SPREADS}
    body: dict[str, object] = {
        "label": "measured on simulated days with no events; not a guarantee about real data",
        "seed": SEED,
        "series": TYPES * DISTRICTS,
        "test_days": COLUMNS - LEAD_IN,
        "dispersion": "negative binomial, variance = mean + mean^2/k, k shared within a "
        "complaint type; spread is the variance as a multiple of Poisson for a series with mean 9",
        "share_of_days_with_any_flag": rates,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=2) + "\n")
    return body
