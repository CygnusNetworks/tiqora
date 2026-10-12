"""Schemas of ``/api/v1/itsm``."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from tiqora.api.v1.admin.itsm_schemas import LinkPropagation


class LinkTypeOptionOut(BaseModel):
    """A link type offered for an object pair. Labels: override, else i18n, else Znuny's name."""

    name: str
    source_label: str | None
    target_label: str | None
    source_name: str | None
    target_name: str | None
    pointed: bool
    propagation: LinkPropagation
    origin: Literal["znuny", "tiqora"]
