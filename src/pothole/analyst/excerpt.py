"""The short form of a tool result that the finding keeps as its evidence.

A result that is a list, or an object holding a list, is shortened between items and says how
many of them are shown, so a reader never meets a list cut in the middle of an item. A daily
series keeps its latest days (the spike is at the end); any other list keeps its first items.
"""

import json
from typing import Any

from pothole.repair.tools import cap

TAIL_KEYS = ("series",)


OMITTED = "no whole item fits here, so none is shown"


def _fit(items: list[Any], rebuild: Any, limit: int, from_end: bool) -> str | None:
    """The longest run of whole items for which rebuild(kept, label, note) fits `limit`. When
    not even one fits, none is shown and the text says so: an item is never cut."""
    for n in range(len(items) - 1, -1, -1):
        kept = items[len(items) - n :] if from_end else items[:n]
        label = f"{'last' if from_end else 'first'} {n} of {len(items)}"
        text: str = rebuild(kept, label, OMITTED if n == 0 else None)
        if len(text) <= limit:
            return text
    return None


def excerpt(result: str, limit: int) -> str:
    if len(result) <= limit:
        return result
    try:
        value = json.loads(result)
    except json.JSONDecodeError:
        return cap(result, limit - 1)
    shortened: str | None = None
    if isinstance(value, list):
        shortened = _fit(
            value,
            lambda kept, label, note: f"{json.dumps(kept)} ({label}{f'; {note}' if note else ''})",
            limit,
            False,
        )
    elif isinstance(value, dict):
        lists = {k: v for k, v in value.items() if isinstance(v, list)}
        if lists:
            key = max(lists, key=lambda k: len(json.dumps(lists[k])))
            shortened = _fit(
                lists[key],
                lambda kept, label, note: json.dumps(
                    {**value, key: kept, "shown": label, **({"omitted": note} if note else {})}
                ),
                limit,
                key in TAIL_KEYS,
            )
    return shortened if shortened is not None else cap(result, limit - 1)
