"""The words the repair agent is given: its instructions, its tools and its nudges."""

from typing import Any

SYSTEM_PROMPT = """\
You repair a daily data pipeline over public NYC 311 service requests. A check on today's batch \
failed. Decide one of three things and submit it with the submit tool:

- patch: the source changed shape and the pipeline spec can be fixed with the edit operations below.
- issue: something is wrong that a person must look at, or no allowed edit can fix it.
- no_change: the data is as it should be (for example a real spike) and nothing should change.

You can only propose these edit operations, each on one column of the spec:
- map_field {column, source}: read the column from a different source field (a rename). A source \
field can feed only one column. The key column cannot be remapped.
- set_format {column, format}: change how a timestamp or number column is read. Timestamp formats: \
%Y-%m-%dT%H:%M:%S.%f, %Y-%m-%dT%H:%M:%S, %Y-%m-%d %H:%M:%S, %Y-%m-%d, %m/%d/%Y %I:%M:%S %p, \
%m/%d/%Y %H:%M:%S, %m/%d/%Y, %d-%b-%Y. Number formats: plain, comma_thousands.
- add_value_mapping {column, raw, value}: add one listed value (raw -> value) to a text column \
that already has a list of values; value must already be one of that column's values.

You can never change the checks, the source, add columns or write code. Anything else is refused. \
Never loosen what the data is checked against: if the only way out is to change a check, submit \
an issue.

Your tools: read_spec, read_check_results, source_fields (each field with its type and whether it \
is in_use by a column, known_unused because the spec lists it as a field that holds other data, or \
new), sample_rows, formats_that_parse (the fixed formats under which at least 99% of a column's \
present values parse on the failing batch), try_patch and submit.

Rules the edits must follow: an add_value_mapping value must be one of that column's existing \
values (the right-hand side of its list), never a new spelling. map_field may not read a \
known_unused field: it holds different data, and a renamed field appears as a new one. Reading a \
field another column uses is allowed.

When the only failing check is the volume check, the count is above the usual band and every \
other check passes, the right answer is usually no_change (real activity), explained in the \
reason.

How to work: read the check results, look at the spec, the source fields and a few failing rows, \
then try your edits with try_patch and look at the checks it returns. A patch is accepted \
only when no check fails on the failing batch afterwards. You may edit only a column that a \
failed check names, and map_field may only read a field the failing batch actually sends. \
Submit a patch with \
your edits and a reason; the regression test rows are chosen for you. The reason is one or two \
plain sentences that say what changed in the data and what the edit does. Every reply you send, \
including errors, uses up one of a small number of calls; when two remain you must submit.\
"""

REPLIES_LEFT = "You have {n} calls left."
SUBMIT_NEXT = "You have 2 calls left: call submit now."
LAST_REPLY = "This is your last call: submit now."
NUDGE_NO_TOOL = "Call one of the tools."
NUDGE_LENGTH = "Your reply was cut off. Answer with a shorter tool call."

_OP: dict[str, Any] = {
    "type": "object",
    "properties": {
        "op": {"type": "string", "enum": ["map_field", "set_format", "add_value_mapping"]},
        "column": {"type": "string", "description": "a column name of the spec"},
        "source": {"type": "string", "description": "map_field: the source field to read"},
        "format": {"type": "string", "description": "set_format: the format"},
        "raw": {
            "type": "string",
            "description": "add_value_mapping: the value as the source has it",
        },
        "value": {"type": "string", "description": "add_value_mapping: the value to store"},
    },
    "required": ["op", "column"],
}


def _tool(
    name: str, description: str, properties: dict[str, Any], required: list[str]
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


TOOLS: list[dict[str, Any]] = [
    _tool("read_spec", "The current pipeline spec as YAML.", {}, []),
    _tool("read_check_results", "The checks that did not pass on the failing batch.", {}, []),
    _tool(
        "sample_rows",
        "Values of one source field in rows of the failing batch.",
        {
            "column": {"type": "string", "description": "a source field name"},
            "only_failing": {"type": "boolean", "description": "only rows named by failed checks"},
            "n": {"type": "integer", "description": "how many rows, at most 20"},
        },
        ["column", "only_failing", "n"],
    ),
    _tool(
        "source_fields",
        "The source's current fields: type, and status in_use (a column reads it), known_unused "
        "(listed as holding other data) or new.",
        {},
        [],
    ),
    _tool(
        "formats_that_parse",
        "The fixed formats under which at least 99% of a timestamp or number column's present "
        "values parse on the failing batch.",
        {"column": {"type": "string", "description": "a column name of the spec"}},
        ["column"],
    ),
    _tool(
        "try_patch",
        "Apply edits to a copy of the spec, run the checks on the failing batch, return them.",
        {"ops": {"type": "array", "items": _OP}},
        ["ops"],
    ),
    _tool(
        "submit",
        "Submit the decision. A patch is checked before it is accepted.",
        {
            "action": {"type": "string", "enum": ["patch", "issue", "no_change"]},
            "ops": {"type": "array", "items": _OP, "description": "edits; empty unless patch"},
            "reason": {"type": "string"},
        },
        ["action", "reason"],
    ),
]
