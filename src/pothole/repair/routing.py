"""Who handles a failed run: a rule, the repair agent, an issue, or nobody."""

import re
from collections.abc import Callable
from typing import Any, Literal

from pothole.repair.outcome import RepairOutcome
from pothole.spec.checks import CheckResult

Route = Literal["agent", "issue", "nothing"]

_VOLUME = re.compile(r"^(\d+) rows against a typical (\S+) over .*allowed (\S+) to (\S+)$")


def route(check_results: list[CheckResult], fetch_error: BaseException | str | None) -> Route:
    """A fetch failure or an empty fetch is an outage: it goes to an issue and never to the model.
    Otherwise any failed check goes to the agent, and a run with none needs nothing."""
    if fetch_error is not None:
        return "issue"
    return "agent" if any(r.level == "fail" for r in check_results) else "nothing"


def rule_outcome(failed: list[CheckResult]) -> RepairOutcome | None:
    """When the volume check is the only failed check, the code decides from its direction:
    below the band is missing rows (an issue), above it is a real rise (no change). No repairer
    is called. Anything else is for a repairer, so this returns None."""
    if len(failed) != 1 or failed[0].name != "volume":
        return None
    m = _VOLUME.match(failed[0].detail)
    if m is None:
        return None
    count, usual = int(m.group(1)), m.group(2)
    if count < float(m.group(3)):
        reason = f"rows are missing: {count} against a usual {usual}"
        return RepairOutcome("issue", reason=reason, decided_by="rule")
    reason = f"a real rise: {count} against a usual {usual}; every other check passes"
    return RepairOutcome("no_change", reason=reason, decided_by="rule")


def ruled(repairer: Callable[..., RepairOutcome]) -> Callable[..., RepairOutcome]:
    """A repairer that lets `rule_outcome` decide first (its arguments: spec, failed checks,
    sample rows, source fields)."""

    def decide(spec: Any, failed: list[CheckResult], *rest: Any) -> RepairOutcome:
        return rule_outcome(failed) or repairer(spec, failed, *rest)

    return decide
