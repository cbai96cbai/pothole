"""The text of a pull request, an issue and a regression file."""

import json
from typing import Any

from pothole.repair.outcome import RegressionCase, RepairOutcome
from pothole.spec.checks import CheckResult

SPEC_PATH = "pipelines/311.yaml"
NOT_JUDGED = "proposed, not judged"
OUTPUT_CHARS = 3000


def results_block(results: list[CheckResult]) -> str:
    shown = [r for r in results if r.level != "pass"]
    if not shown:
        return "All checks pass."
    return "\n".join(f"- {r.level} `{r.name}`: {r.detail}" for r in shown)


def ops_block(outcome: RepairOutcome) -> str:
    return "\n".join(f"- `{o.model_dump_json()}`" for o in outcome.ops)


def regression_file(case: RegressionCase) -> str:
    data: dict[str, Any] = {"spec": SPEC_PATH, "rows": case.rows, "expected": case.expected}
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def pr_title(outcome: RepairOutcome, drill: bool) -> str:
    first = outcome.reason.strip().splitlines()[0][:70]
    return f"Drill: {first}" if drill else f"Repair: {first}"


def pr_body(
    outcome: RepairOutcome,
    before: list[CheckResult],
    after: list[CheckResult],
    *,
    drill: bool,
    verdict: str | None,
    sha: str | None,
    tests: str | None,
) -> str:
    parts = [
        "**Drill: this failure was injected on purpose.**" if drill else f"**{NOT_JUDGED}**",
        f"Verdict: {verdict}" if verdict else f"Verdict: none yet ({NOT_JUDGED}).",
        f"## Reasoning\n{outcome.reason}",
        f"## Edits\n{ops_block(outcome)}",
        f"## Checks before\n{results_block(before)}",
        f"## Checks after\n{results_block(after)}",
    ]
    if tests is not None:
        parts.append(f"## Test output on `{sha}`\n```\n{tests[-OUTPUT_CHARS:]}\n```")
    return "\n\n".join(parts) + "\n"


def issue_body(reason: str, failing: list[CheckResult], extra: str | None = None) -> str:
    parts = [f"## Reason\n{reason}", f"## Failing checks\n{results_block(failing)}"]
    if extra:
        parts.append(f"## Output\n```\n{extra[-OUTPUT_CHARS:]}\n```")
    return "\n\n".join(parts) + "\n"
