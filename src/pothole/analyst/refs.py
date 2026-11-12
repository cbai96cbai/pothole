"""Evidence refs: the numbers the investigator shows the model at the start of each tool result.

The model cites a call by its number. Only a call that succeeded, in an earlier reply than the
submission, can be cited: the model has read its result by then.
"""

import re
from typing import Any

from pothole.analyst.tools import ToolCall
from pothole.repair.validate import ToolError


def prefixed(ref: int, content: str) -> str:
    return f"ref {ref}: {content}"


ECHO = 12  # how much of a refused ref is repeated back
MAX_REF_DIGITS = 6  # a longer digit string is not a ref (and is costly to convert)


def _number(ref: Any) -> int | None:
    if isinstance(ref, int) and not isinstance(ref, bool):
        return ref
    if isinstance(ref, str) and re.fullmatch(rf"[0-9]{{1,{MAX_REF_DIGITS}}}", ref):
        return int(ref)
    return None


def cited(refs: Any, calls: dict[int, ToolCall], seen: int) -> list[int]:
    """The distinct ref numbers in `refs`, in order, or a ToolError saying what can be cited.
    `seen` is the highest ref whose result the model has already been shown."""
    citable = {n: c for n, c in calls.items() if n <= seen}
    if not citable:
        raise ToolError(
            "no refs you can cite yet: call a tool, read its result, then submit in a separate "
            "reply"
        )
    listing = "refs you can cite: " + ", ".join(f"{n} {c.name}" for n, c in sorted(citable.items()))
    if not isinstance(refs, list) or not refs:
        raise ToolError(
            f"evidence must list the ref numbers of the tool calls you rely on; {listing}"
        )
    numbers = [_number(r) for r in refs]
    bad = [str(r) for r, n in zip(refs, numbers, strict=True) if n is None or n not in citable]
    if bad:
        unread = [n for n in numbers if n is not None and n in calls and n not in citable]
        hint = (
            "; read results first and submit in a separate reply from the calls" if unread else ""
        )
        shown = ", ".join(b[:ECHO] for b in bad)
        raise ToolError(f"evidence names refs that do not exist: {shown}{hint}; {listing}")
    return list(dict.fromkeys(n for n in numbers if n is not None))
