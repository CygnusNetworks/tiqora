"""ITSM endpoints for agents — ``/api/v1/itsm`` (API-key area ``cmdb``, step 0)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from tiqora.api.deps import CurrentUser, DbSession
from tiqora.api.v1.itsm_schemas import LinkTypeOptionOut
from tiqora.itsm.link_types import LinkPair, offered_link_types
from tiqora.itsm.mode import get_itsm_mode

router = APIRouter(prefix="/itsm", tags=["itsm"])


@router.get("/link-types", response_model=list[LinkTypeOptionOut])
async def get_offered_link_types(
    user: CurrentUser,
    session: DbSession,
    pair: Annotated[LinkPair, Query(description="Object pair the link is created for")] = (
        "ticket_ticket"
    ),
) -> list[LinkTypeOptionOut]:
    """The link types the picker offers for *pair* in the current ITSM mode."""
    _ = user
    state = await get_itsm_mode(session)
    if state.mode == "off" and pair != "ticket_ticket":
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"code": "itsm_off"})
    options = await offered_link_types(session, pair=pair, mode=state.mode)
    return [
        LinkTypeOptionOut(
            name=o.name,
            source_label=o.source_label,
            target_label=o.target_label,
            source_name=o.source_name,
            target_name=o.target_name,
            pointed=o.pointed,
            propagation=o.propagation,
            origin=o.origin,
        )
        for o in options
    ]
