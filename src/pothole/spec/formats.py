"""Which fixed formats read a column's values: worked out from the batch, not guessed."""

from collections.abc import Iterable

from pothole.spec.engine import cast_value
from pothole.spec.model import DATE_FORMATS, NUMBER_FORMATS, Column

SHARE = 0.99  # a format counts when at least this share of the present values parse under it


def formats_that_parse(col: Column, values: Iterable[str | None]) -> list[str]:
    """The fixed formats for a timestamp, int or float column under which at least 99% of the
    present (non-empty) values parse. Empty when there are no present values."""
    fixed = {"timestamp": DATE_FORMATS, "int": NUMBER_FORMATS, "float": NUMBER_FORMATS}
    present = [v for v in values if v]
    if not present:
        return []
    out: list[str] = []
    for fmt in fixed.get(col.type, ()):
        trial = col.model_copy(update={"format": fmt})
        ok = 0
        for v in present:
            try:
                cast_value(trial, v)
            except ValueError:
                continue
            ok += 1
        if ok / len(present) >= SHARE:
            out.append(fmt)
    return out
