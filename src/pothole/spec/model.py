"""The pipeline spec: what the source is, how its fields become columns, and what must hold."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

DATE_FORMATS: tuple[str, ...] = (
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
    "%m/%d/%Y %I:%M:%S %p",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y",
    "%d-%b-%Y",
)
NUMBER_FORMATS: tuple[str, ...] = ("plain", "comma_thousands")

ColumnType = Literal["text", "int", "float", "timestamp"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Source(_Strict):
    domain: str
    dataset: str
    key: str
    window_days: int = Field(gt=0)


class Column(_Strict):
    name: str
    source: str
    type: ColumnType
    required: bool = False
    format: str | None = None
    values: dict[str, str] | None = None

    @model_validator(mode="after")
    def _format_and_values_fit_the_type(self) -> "Column":
        if self.format is not None:
            allowed = {"timestamp": DATE_FORMATS, "int": NUMBER_FORMATS, "float": NUMBER_FORMATS}
            if self.format not in allowed.get(self.type, ()):
                raise ValueError(
                    f"column {self.name}: format {self.format!r} not allowed for {self.type}"
                )
        if self.values is not None and self.type != "text":
            raise ValueError(f"column {self.name}: values apply to text columns only")
        return self


class Volume(_Strict):
    weeks: int = 4
    low: float = 0.5
    high: float = 2.0


class Checks(_Strict):
    schema_check: bool = Field(default=True, alias="schema")
    freshness_max_hours: float | None = None
    volume: Volume | None = None
    null_rate_max: dict[str, float] = Field(default_factory=dict)
    ranges: dict[str, tuple[float, float]] = Field(default_factory=dict)
    created_not_future: bool = False
    closed_not_before_created: bool = False
    unique_key: bool = False

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class PipelineSpec(_Strict):
    source: Source
    columns: list[Column]
    checks: Checks

    @model_validator(mode="after")
    def _names_resolve(self) -> "PipelineSpec":
        names = [c.name for c in self.columns]
        if len(set(names)) != len(names):
            raise ValueError("duplicate column names")
        known = set(names)
        if self.source.key not in known:
            raise ValueError(f"source.key {self.source.key!r} is not a column")
        named = [*self.checks.null_rate_max, *self.checks.ranges]
        for name in named:
            if name not in known:
                raise ValueError(f"check names unknown column {name!r}")
        return self

    @classmethod
    def from_yaml(cls, text: str) -> "PipelineSpec":
        return cls.model_validate(yaml.safe_load(text))

    @classmethod
    def load(cls, path: Path) -> "PipelineSpec":
        return cls.from_yaml(path.read_text())

    def to_yaml(self) -> str:
        return yaml.safe_dump(self.model_dump(by_alias=True, mode="json"), sort_keys=False)

    def column(self, name: str) -> Column:
        for c in self.columns:
            if c.name == name:
                return c
        raise KeyError(name)
