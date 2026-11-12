"""Check a model's tool arguments against the tool's own declared schema.

Each property that is present must have the declared type, be one of the declared values, and sit
inside the declared range. Anything else is a ToolError the model reads, so a wrong argument can
never reach the tool as a surprise type. Items inside an array are the tool's to check.
"""

from typing import Any

from pothole.repair.validate import ToolError

_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
}
_ARTICLE = {
    "string": "a string",
    "integer": "an integer",
    "number": "a number",
    "boolean": "true or false",
    "array": "a list",
    "object": "an object",
}


def check_arguments(tools: list[dict[str, Any]], name: str, args: dict[str, Any]) -> None:
    """Raise ToolError if an argument of tool `name` does not fit its schema. An unknown tool
    name is left to the caller."""
    schema = next((t["function"] for t in tools if t["function"]["name"] == name), None)
    if schema is None:
        return
    for key, prop in schema["parameters"]["properties"].items():
        if key in args:
            _check(key, args[key], prop)


def _check(key: str, value: Any, prop: dict[str, Any]) -> None:
    kind = prop["type"]
    ok = isinstance(value, _TYPES[kind]) and (kind in ("boolean",) or not isinstance(value, bool))
    if not ok:
        raise ToolError(f"{key} must be {_ARTICLE[kind]}")
    if "enum" in prop and value not in prop["enum"]:
        raise ToolError(f"{key} must be one of {', '.join(map(str, prop['enum']))}")
    low, high = prop.get("minimum"), prop.get("maximum")
    if low is not None and value < low or high is not None and value > high:
        raise ToolError(f"{key} must be from {low} to {high}")
