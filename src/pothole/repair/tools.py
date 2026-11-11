"""The agent's tools. They see the spec, the failed checks, the failing batch and the source's
field list: nothing else."""

import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any, get_args

from pothole.repair.outcome import Action, Op, RegressionCase
from pothole.repair.validate import ToolError, build_case, failed
from pothole.spec import engine, ops, scope
from pothole.spec.checks import CheckResult
from pothole.spec.formats import formats_that_parse
from pothole.spec.model import PipelineSpec
from pothole.spec.normalise import board

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
        now: datetime | None,
        history: list[int],
    ) -> None:
        self.spec, self.rows, self.results = spec, rows, results
        self.fields, self.now, self.history = source_fields, now, history

    def run(self, name: str, args: dict[str, Any]) -> str:
        if name == "read_spec":
            return self.spec.to_yaml()
        if name == "read_check_results":
            return render_results(self.results)
        if name == "source_fields":
            return json.dumps(self._field_report(), sort_keys=True)
        if name == "value_crosstab":
            return self._crosstab(args)
        if name == "formats_that_parse":
            return self._formats(args)
        if name == "sample_rows":
            return self._sample(args)
        if name == "try_patch":
            return render_results(self._patched(args.get("ops"))[1])
        raise ToolError(f"unknown tool {name!r}")

    def _field_report(self) -> dict[str, dict[str, str]]:
        """Each source field with its type and use: `in_use` (a column reads it), `known_unused`
        (the spec lists it in source.ignore) or `new` (neither)."""
        used = {c.source for c in self.spec.columns}
        ignored = set(self.spec.source.ignore)
        return {
            name: {
                "type": kind,
                "status": "in_use"
                if name in used
                else "known_unused"
                if name in ignored
                else "new",
            }
            for name, kind in self.fields.items()
        }

    def _crosstab(self, args: dict[str, Any]) -> str:
        """Each value outside the column's list against the borough it appears with."""
        name = args.get("column")
        if not isinstance(name, str) or name not in {c.name for c in self.spec.columns}:
            raise ToolError("value_crosstab needs column: a column name of the spec")
        col = self.spec.column(name)
        if col.values is None:
            raise ToolError(f"{name} has no listed values")
        by_name = {c.name: c.source for c in self.spec.columns}
        board_src, zip_src = by_name.get("community_board"), by_name.get("incident_zip")
        counts: dict[str, Counter[str]] = {}
        for row in self.rows:
            raw = row.get(col.source)
            if not raw or raw in col.values:
                continue
            found = board(row.get(board_src or ""))
            zipcode = (row.get(zip_src or "") or "")[:3]
            if found:
                where = found[0]
            elif zipcode.isdigit() and len(zipcode) == 3:
                where = f"zip {zipcode}"
            else:
                where = "unknown"
            counts.setdefault(raw, Counter())[where] += 1
        shown = sorted(counts.items(), key=lambda kv: -sum(kv[1].values()))[:MAX_SAMPLE]
        return json.dumps({v: c.most_common(5) for v, c in shown})

    def _formats(self, args: dict[str, Any]) -> str:
        name = args.get("column")
        if not isinstance(name, str) or name not in {c.name for c in self.spec.columns}:
            raise ToolError("formats_that_parse needs column: a column name of the spec")
        col = self.spec.column(name)
        if col.type == "text":
            raise ToolError(f"{name} is a text column; it has no format")
        present = [v for r in self.rows if (v := r.get(col.source))]
        found = formats_that_parse(col, present)
        return json.dumps({"column": name, "present_values": len(present), "formats": found})

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
            self._allowed(parsed)
            patched = ops.apply(self.spec, parsed)
        except ops.ForbiddenEdit as exc:
            raise ToolError(f"ForbiddenEdit: {exc}") from exc
        return (patched, parsed), engine.run(patched, self.rows, self.history, self.now).checks

    def _allowed(self, parsed: list[Op]) -> None:
        """The shared scope rule: only columns with a failed check, only fields the batch sends."""
        sent = {f for r in self.rows for f in r}
        problems = scope.check(list(parsed), self.results, sent, self.spec)
        if problems:
            raise ops.ForbiddenEdit("; ".join(problems))

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
        case = build_case(self.spec, patched, self.rows, [o.column for o in parsed])
        return Submission("patch", parsed, case, reason.strip())
