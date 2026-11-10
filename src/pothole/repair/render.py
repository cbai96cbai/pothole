"""The text of a pull request, an issue and a regression file."""

import json
import re
from typing import Any

from pothole.repair.outcome import RegressionCase, RepairOutcome
from pothole.spec.checks import CheckResult

SPEC_PATH = "pipelines/311.yaml"
NOT_JUDGED = "Proposed, not judged"
LABELS_REAL = ("repair", "not-judged")
LABELS_DRILL = ("drill",)
OUTPUT_CHARS = 3000


def fenced(text: str) -> str:
    """Text in a code block whose fence is longer than any run of backticks inside it, so model
    or tool text can neither close the block nor render a mention or link."""
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}\n{text}\n{fence}"


def labels(drill: bool) -> tuple[str, ...]:
    return LABELS_DRILL if drill else LABELS_REAL


def results_block(results: list[CheckResult]) -> str:
    shown = [r for r in results if r.level != "pass"]
    if not shown:
        return "All checks pass."
    return fenced("\n".join(f"{r.level} {r.name}: {r.detail}" for r in shown))


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
        f"Verdict:\n{fenced(verdict)}" if verdict else f"Verdict: none yet. {NOT_JUDGED}.",
        f"## Reasoning\n{fenced(outcome.reason)}",
        f"## Edits\n{ops_block(outcome)}",
        f"## Checks before\n{results_block(before)}",
        f"## Checks after\n{results_block(after)}",
    ]
    if tests is not None:
        parts.append(f"## Test output on `{sha}`\n{fenced(tests[-OUTPUT_CHARS:])}")
    return "\n\n".join(parts) + "\n"


def issue_body(reason: str, failing: list[CheckResult], extra: str | None = None) -> str:
    parts = [f"## Reason\n{fenced(reason)}", f"## Failing checks\n{results_block(failing)}"]
    if extra:
        parts.append(f"## Output\n{fenced(extra[-OUTPUT_CHARS:])}")
    return "\n\n".join(parts) + "\n"
