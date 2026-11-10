"""The only edits the repair agent may make: three fixed operations on columns."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from pothole.spec.model import DATE_FORMATS, NUMBER_FORMATS, PipelineSpec

__all__ = [
    "DATE_FORMATS",
    "NUMBER_FORMATS",
    "AddValueMapping",
    "EditOp",
    "ForbiddenEdit",
    "MapField",
    "SetFormat",
    "apply",
    "parse",
]


class ForbiddenEdit(Exception):
    """An edit that names the checks, the source, an unknown column or an unknown operation."""


class _Op(BaseModel):
    model_config = ConfigDict(extra="forbid")
    column: str


class MapField(_Op):
    op: Literal["map_field"] = "map_field"
    source: str = Field(min_length=1)


class SetFormat(_Op):
    op: Literal["set_format"] = "set_format"
    format: str


class AddValueMapping(_Op):
    op: Literal["add_value_mapping"] = "add_value_mapping"
    raw: str
    value: str


EditOp = Annotated[MapField | SetFormat | AddValueMapping, Field(discriminator="op")]
_ADAPTER: TypeAdapter[MapField | SetFormat | AddValueMapping] = TypeAdapter(EditOp)
_OPS = {"map_field", "set_format", "add_value_mapping"}


def parse(raw: dict[str, Any]) -> MapField | SetFormat | AddValueMapping:
    """Read one submitted edit; anything but exactly one of the three operations is refused."""
    if raw.get("op") not in _OPS:
        raise ForbiddenEdit(f"unknown operation {raw.get('op')!r}; allowed: {sorted(_OPS)}")
    try:
        return _ADAPTER.validate_python(raw)
    except ValidationError as err:
        raise ForbiddenEdit(f"edit not accepted: {err.errors()[0]['msg']} ({raw})") from err


def apply(spec: PipelineSpec, ops: list[MapField | SetFormat | AddValueMapping]) -> PipelineSpec:
    """Return a new spec with the edits made; the original and its checks are never changed."""
    columns = {c.name: c.model_copy(deep=True) for c in spec.columns}
    for op in ops:
        if op.column not in columns:
            raise ForbiddenEdit(f"{op.op}: column {op.column!r} is not a column of this spec")
        col = columns[op.column]
        if isinstance(op, MapField):
            col.source = op.source
        elif isinstance(op, SetFormat):
            fixed = {"timestamp": DATE_FORMATS, "int": NUMBER_FORMATS, "float": NUMBER_FORMATS}
            if op.format not in fixed.get(col.type, ()):
                raise ForbiddenEdit(
                    f"set_format: {op.format!r} is not a format for {col.type} column {col.name}"
                )
            col.format = op.format
        else:
            if col.type != "text":
                raise ForbiddenEdit(f"add_value_mapping: column {col.name} is {col.type}, not text")
            col.values = {**(col.values or {}), op.raw: op.value}
    edited = PipelineSpec(
        source=spec.source.model_copy(deep=True),
        columns=[columns[c.name] for c in spec.columns],
        checks=spec.checks.model_copy(deep=True),
    )
    if edited.checks != spec.checks or edited.source != spec.source:
        raise ForbiddenEdit("an edit changed the checks or the source")
    return edited
