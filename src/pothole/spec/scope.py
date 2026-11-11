"""The one scope rule for every repairer: an edit may touch only what the failed checks name."""

from collections.abc import Collection

from pothole.spec.checks import CheckResult
from pothole.spec.model import PipelineSpec
from pothole.spec.ops import EditOp, MapField


def failing_columns(spec: PipelineSpec, checks: list[CheckResult]) -> set[str]:
    """Columns with a failed check: `required:`, `cast:`, `values:`, `range:` or `null_rate:`
    named for them, or a schema failure that names the column's source field."""
    by_source = {c.source: c.name for c in spec.columns}
    out: set[str] = set()
    for check in checks:
        if check.level != "fail":
            continue
        if check.name == "schema":
            listed = check.detail.rsplit(": ", 1)[1].split(",")
            out |= {by_source[s.strip()] for s in listed if s.strip() in by_source}
        elif check.name.split(":", 1)[0] in ("required", "cast", "values", "range", "null_rate"):
            out.add(check.name.split(":", 1)[1])
    return out


def check(
    ops: list[EditOp],
    failing_checks: list[CheckResult],
    seen_fields: Collection[str],
    spec: PipelineSpec,
) -> list[str]:
    """Problems with the edits (empty means in scope): a column with no failed check on the seen
    batch, a `map_field` source the seen batch does not send, or one the spec lists in
    `source.ignore` (a known field that holds different data; a renamed field is a new one)."""
    allowed = failing_columns(spec, failing_checks)
    problems: list[str] = []
    for op in ops:
        if op.column not in allowed:
            problems.append(f"{op.op}: column {op.column!r} has no failed check")
        if isinstance(op, MapField) and op.source not in seen_fields:
            problems.append(f"map_field: the batch does not send {op.source!r}")
        if isinstance(op, MapField) and op.source.startswith(":"):
            problems.append(f"map_field: {op.source!r} is the source's own system field")
        if isinstance(op, MapField) and op.source in spec.source.ignore:
            problems.append(
                f"map_field: {op.source!r} is a known field that holds other data, not a rename"
            )
    return problems
