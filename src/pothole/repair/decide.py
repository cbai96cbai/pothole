"""The one door every decision passes through: the volume rule first, then the given repairer.

The eval runner, the scorer, the daily real-failure path and the daily drill all call `decide`,
so no path can reach a repairer with a failure the code already settles.
"""

from collections.abc import Callable
from typing import Any

from pothole.repair.outcome import RepairOutcome
from pothole.spec.checks import CheckResult, read_volume
from pothole.spec.model import PipelineSpec

Repairer = Callable[
    [PipelineSpec, list[CheckResult], list[dict[str, Any]], dict[str, str]], RepairOutcome
]


def rule_outcome(failed: list[CheckResult]) -> RepairOutcome | None:
    """When the volume check is the only failed (level fail) check, the code decides from its
    direction: below the band is missing rows (an issue), above it a real rise (no change). Any
    other failure is for a repairer: None. A failed volume check whose text cannot be read
    raises; it never falls through to a repairer."""
    fails = [c for c in failed if c.level == "fail"]
    volume = next((c for c in fails if c.name == "volume"), None)
    if volume is None:
        return None
    reading = read_volume(volume.detail)
    if len(fails) != 1:
        return None
    usual = f"{reading.typical:g}"
    if reading.count < reading.low:
        reason = f"rows are missing: {reading.count} against a usual {usual}"
        return RepairOutcome("issue", reason=reason, decided_by="rule")
    reason = f"a real rise: {reading.count} against a usual {usual}; every other check passes"
    return RepairOutcome("no_change", reason=reason, decided_by="rule")


def decide(
    repairer: Repairer,
    spec: PipelineSpec,
    failed: list[CheckResult],
    rows: list[dict[str, Any]],
    fields: dict[str, str],
) -> RepairOutcome:
    """The volume rule if it applies, else the repairer (the agent or the baseline)."""
    return rule_outcome(failed) or repairer(spec, failed, rows, fields)
