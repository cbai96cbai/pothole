"""The agent's tools. They see the spec, the failed checks, the failing batch and the source's
field list: nothing else."""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, get_args

from pothole.repair.outcome import Action, Op, RegressionCase
from pothole.repair.validate import Batch, ToolError, build_case, check_healthy, failed
from pothole.spec import engine, ops
from pothole.spec.checks import CheckResult
from pothole.spec.model import PipelineSpec

MAX_SAMPLE = 20
MAX_TRACE_CHARS = 2000


def cap(text: str, limit: int = MAX_TRACE_CHARS) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


def render_results(results: list[CheckResult]) -> str:
    shown = [r for r in results if r.level != "pass"]
    if not shown:
        return "all checks pass"
    return "\n".join(
        f"{r.level} {r.name}: {r.detail}"
        + (f" (keys {', '.join(r.sample_keys)})" if r.sample_keys else "")
        for r in shown
    )


@dataclass(frozen=True)
class Submission:
    action: Action
    ops: list[Op]
    regression: RegressionCase | None
    reason: str


class Toolbox:
    def __init__(
        self,
        spec: PipelineSpec,
        rows: list[dict[str, str]],
        results: list[CheckResult],
        source_fields: dict[str, str],
        healthy: list[Batch],
        now: datetime | None,
        history: list[int],
    ) -> None:
        self.spec, self.rows, self.results = spec, rows, results
        self.fields, self.healthy, self.now, self.history = source_fields, healthy, now, history

    def run(self, name: str, args: dict[str, Any]) -> str:
        if name == "read_spec":
            return self.spec.to_yaml()
        if name == "read_check_results":
            return render_results(self.results)
        if name == "source_fields":
            return json.dumps(self.fields, sort_keys=True)
        if name == "sample_rows":
            return self._sample(args)
        if name == "try_patch":
            return render_results(self._patched(args.get("ops"))[1])
        raise ToolError(f"unknown tool {name!r}")

    def _sample(self, args: dict[str, Any]) -> str:
        column, n = args.get("column"), args.get("n", 5)
        if not isinstance(column, str) or not isinstance(n, int) or isinstance(n, bool):
            raise ToolError("sample_rows needs column (a string) and n (an integer)")
        if column not in self.fields and not any(column in r for r in self.rows):
            raise ToolError(f"{column!r} is not a source field; see source_fields")
        key_field = self.spec.column(self.spec.source.key).source
        chosen = self.rows
        if args.get("only_failing"):
            keys = {k for r in self.results if r.level != "pass" for k in r.sample_keys}
            chosen = [r for r in self.rows if r.get(key_field) in keys]
            chosen = chosen or [r for r in self.rows if not r.get(column)]
        out = [
            {"key": r.get(key_field), "value": r.get(column)}
            for r in chosen[: max(1, min(n, MAX_SAMPLE))]
        ]
        return json.dumps(out)

    def _patched(self, raw: Any) -> tuple[tuple[PipelineSpec, list[Op]], list[CheckResult]]:
        if not isinstance(raw, list) or not raw or not all(isinstance(o, dict) for o in raw):
            raise ToolError("ops must be a non-empty list of edit objects")
        try:
            parsed = [ops.parse(o) for o in raw]
            patched = ops.apply(self.spec, parsed)
        except ops.ForbiddenEdit as exc:
            raise ToolError(f"ForbiddenEdit: {exc}") from exc
        return (patched, parsed), engine.run(patched, self.rows, self.history, self.now).checks

    def submit(self, args: dict[str, Any]) -> Submission:
        action: Any = args.get("action")
        reason = args.get("reason")
        if action not in get_args(Action):
            raise ToolError("action must be patch, issue or no_change")
        if not isinstance(reason, str) or not reason.strip():
            raise ToolError("reason is required")
        if action != "patch":
            if args.get("ops"):
                raise ToolError("ops are for a patch only")
            return Submission(action, [], None, reason.strip())
        (patched, parsed), results = self._patched(args.get("ops"))
        still = failed(results)
        if still:
            raise ToolError(
                "the patched spec still fails on the failing batch:\n" + render_results(still)
            )
        problems = check_healthy(patched, parsed, self.healthy, self.history)
        if problems:
            raise ToolError(
                "the patched spec fails on healthy earlier batches:\n" + "\n".join(problems[:5])
            )
        keys = args.get("regression_keys")
        if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
            raise ToolError("regression_keys must be a list of key strings")
        case = build_case(self.spec, patched, self.rows, keys, self.now)
        return Submission("patch", parsed, case, reason.strip())
