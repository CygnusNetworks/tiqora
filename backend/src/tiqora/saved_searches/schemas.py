"""Request / response models of ``/api/v1/saved-searches``."""

from __future__ import annotations

from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    field_serializer,
    model_validator,
)

from tiqora.domain.schemas import TicketSortKey, UtcDateTime
from tiqora.filters import Filter
from tiqora.filters.ticket_params import TicketListParams
from tiqora.reporting.schemas import ReportOwnerOut


class SavedSearchIn(BaseModel):
    """Exactly one of ``criteria`` / ``list_params`` ("Ansicht speichern" sends the latter)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    criteria: Filter | None = None
    list_params: TicketListParams | None = None
    include_archived: bool = False
    sort_by: TicketSortKey = "age"
    sort_order: Literal["asc", "desc"] = "desc"
    group_ids: list[int] = Field(default_factory=list, max_length=50)
    #: the caller's own prefs, written on create only (later changes go through ``/prefs``)
    show_in_sidebar: bool = True
    show_count: bool = False

    @model_validator(mode="after")
    def _exactly_one_source(self) -> Self:
        # all TicketListParams fields are optional: ``{}`` is a value, test ``is not None``
        if (self.criteria is not None) == (self.list_params is not None):
            raise ValueError("Bitte entweder criteria oder list_params angeben.")
        return self


class SavedSearchUpdateIn(SavedSearchIn):
    """PUT is a full replace of the search itself (name, criteria, sorting, sharing).

    ``version`` is the optimistic lock. ``show_in_sidebar`` / ``show_count`` are ignored here;
    the caller's own display settings are changed with ``PUT /saved-searches/{id}/prefs``.
    """

    version: int = Field(ge=1)


class SavedSearchPrefsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    show_in_sidebar: bool
    show_count: bool
    #: ``None`` keeps the stored position (a new entry is appended at the end)
    position: int | None = Field(None, ge=0, le=10_000)


class SavedSearchOut(BaseModel):
    id: int
    name: str
    # the wrap serializer below hides the type from the schema generator; restate it
    criteria: Annotated[
        Filter | None,
        Field(
            json_schema_extra={"anyOf": [{"$ref": "#/components/schemas/Filter"}, {"type": "null"}]}
        ),
    ]
    criteria_error: str | None
    include_archived: bool
    sort_by: TicketSortKey
    sort_order: Literal["asc", "desc"]
    owner: ReportOwnerOut
    is_owner: bool
    can_edit: bool
    group_ids: list[int]
    show_in_sidebar: bool
    show_count: bool
    position: int
    source: Literal["tiqora", "znuny"]
    source_name: str | None
    version: int
    changed_at: UtcDateTime

    @field_serializer("criteria", mode="wrap")
    def _criteria_without_nones(
        self, value: Filter | None, _handler: SerializerFunctionWrapHandler
    ) -> Any:
        # ``Filter`` documents ``model_dump(by_alias=True, exclude_none=True)``: a condition
        # without a value must not grow ``"value": null``
        return None if value is None else value.model_dump(by_alias=True, exclude_none=True)


class SavedSearchCountsOut(BaseModel):
    #: ``str(id)`` -> count; ``None`` = could not be counted (invalid criteria, timeout)
    counts: dict[str, int | None]


class UnmappedOut(BaseModel):
    key: str
    value: str
    reason: str


class ZnunyProfilePreviewOut(BaseModel):
    name: str
    # same wrap serializer as ``SavedSearchOut``: no ``"value": null`` in conditions
    criteria: Annotated[
        Filter,
        Field(json_schema_extra={"anyOf": [{"$ref": "#/components/schemas/Filter"}]}),
    ]
    include_archived: bool
    unmapped: list[UnmappedOut]
    #: id of the search imported from this profile earlier -> applying updates it
    existing_id: int | None
    #: ``False`` when no condition could be taken over (or the criteria fail validation)
    importable: bool
    #: some keys were not taken over: the import is broader than the Znuny original, so
    #: clients must not preselect it
    partial: bool

    @field_serializer("criteria", mode="wrap")
    def _criteria_without_nones(
        self, value: Filter, _handler: SerializerFunctionWrapHandler
    ) -> Any:
        return value.model_dump(by_alias=True, exclude_none=True)


class ZnunyImportPreviewOut(BaseModel):
    profiles: list[ZnunyProfilePreviewOut]  # sorted by name


class ZnunyImportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    names: list[Annotated[str, Field(max_length=200)]] = Field(min_length=1, max_length=100)


class ZnunyImportSkippedOut(BaseModel):
    name: str
    reason: str


class ZnunyImportResultOut(BaseModel):
    created: list[int]
    updated: list[int]
    skipped: list[ZnunyImportSkippedOut]
