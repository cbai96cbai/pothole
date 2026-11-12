"""The engine never raises on any source string: a value a column cannot hold is a cast failure."""

import random
from datetime import datetime

import pytest

from pothole.drills import catalog
from pothole.spec import engine, ops

Y = "y_coordinate_state_plane"
SPEC = ops.apply(
    catalog.load_spec(), [ops.parse({"op": "set_format", "column": Y, "format": "comma_thousands"})]
)
ROW = dict(catalog.clean_batch("2026-11-04")[0])
SEEDS = [
    "9223372036854775807", "9223372036854775808", "-9223372036854775808", "-9223372036854775809",
    "1e400", "-1e400", "nan", "NaN", "inf", "-Infinity", "1e5", "1_000", "１２３", "٣٤٥", "0x10",
    "9" * 5000, "1,000", ",,,", "-", "+", ".", "1.5e308", "2e308", "0001-01-01T00:00:00",
    "9999-12-31T23:59:59.999999", "0000-01-01", "2026-13-45T99:99:99", "2026-11-04T00:00:00+05:00",
    "2026-11-04T00:00:00Z", "2026-11-04 25:00", "20261104", "２０２６-11-04", "\x00", "\ud800",
]  # fmt: skip
ALPHABET = "0123456789-+.,eE:T _x٣１nNaAiIfF"


def strings(rng: random.Random, n: int) -> list[str]:
    out = list(SEEDS)
    while len(out) < n:
        out.append("".join(rng.choice(ALPHABET) for _ in range(rng.randint(0, 30))))
    return out


@pytest.mark.parametrize("kind", ["text", "int", "float", "timestamp"])
def test_no_string_makes_the_engine_raise(kind):
    rng = random.Random(7)
    columns = [c for c in SPEC.columns if c.type == kind and c.values is None]
    assert columns
    for raw in strings(rng, 500):
        row = {**ROW, **{c.source: raw for c in columns}}
        result = engine.run(SPEC, [row, dict(ROW)], [], datetime(2026, 11, 5, 12))
        assert result.table.num_rows == 2
