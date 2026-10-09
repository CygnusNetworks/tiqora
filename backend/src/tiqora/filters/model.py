"""Filter language: JSON model (version 1). Pure, no database access."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ConditionOp = Literal[
    "eq",
    "neq",
    "in",
    "not_in",
    "contains",
    "starts_with",
    "is_empty",
    "is_not_empty",
    "lt",
    "lte",
    "gt",
    "gte",
    "between",
    "before",
    "after",
    "within_last",
    "older_than",
    "is_true",
    "is_false",
]


class Condition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=1, max_length=120)
    op: ConditionOp
    value: Any = None


class AllOf(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_mode_override="validation")

    all: list[FilterNode] = Field(min_length=1)


class AnyOf(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_mode_override="validation")

    any: list[FilterNode] = Field(min_length=1)


class Not(BaseModel):
    model_config = ConfigDict(
        extra="forbid", populate_by_name=True, json_schema_mode_override="validation"
    )

    not_: FilterNode = Field(alias="not")


FilterNode = AllOf | AnyOf | Not | Condition


class Filter(BaseModel):
    """Serialise with ``model_dump(by_alias=True, exclude_none=True)``."""

    model_config = ConfigDict(extra="forbid", json_schema_mode_override="validation")

    version: Literal[1] = 1
    where: FilterNode | None = None  # None = no condition (scope only)


AllOf.model_rebuild()
AnyOf.model_rebuild()
Not.model_rebuild()
Filter.model_rebuild()
