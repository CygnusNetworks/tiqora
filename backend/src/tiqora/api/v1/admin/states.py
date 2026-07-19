"""Admin CRUD for ticket states (read-only state types listing included)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from tiqora.api.deps import DbSession
from tiqora.api.v1.admin.common import invalidate_cache_for_state, now
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.api.v1.admin.schemas import StateCreate, StateOut, StateUpdate
from tiqora.db.legacy.ticket import TicketState

router = APIRouter(prefix="/states", tags=["admin:states"])


@router.get("", response_model=list[StateOut])
async def list_states(admin: AdminUser, session: DbSession) -> list[TicketState]:
    _ = admin
    result = await session.execute(select(TicketState).order_by(TicketState.name))
    return list(result.scalars().all())


@router.get("/{state_id}", response_model=StateOut)
async def get_state(state_id: int, admin: AdminUser, session: DbSession) -> TicketState:
    _ = admin
    state = await session.get(TicketState, state_id)
    if state is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="State not found")
    return state


@router.post("", response_model=StateOut, status_code=status.HTTP_201_CREATED)
async def create_state(body: StateCreate, admin: AdminUser, session: DbSession) -> TicketState:
    ts = now()
    state = TicketState(
        **body.model_dump(),
        create_time=ts,
        create_by=admin.id,
        change_time=ts,
        change_by=admin.id,
    )
    session.add(state)
    await session.commit()
    await session.refresh(state)
    return state


@router.patch("/{state_id}", response_model=StateOut)
async def update_state(
    state_id: int, body: StateUpdate, admin: AdminUser, session: DbSession
) -> TicketState:
    state = await session.get(TicketState, state_id)
    if state is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="State not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(state, field, value)
    state.change_time = now()
    state.change_by = admin.id
    await invalidate_cache_for_state(session, state_id)
    await session.commit()
    await session.refresh(state)
    return state


@router.delete("/{state_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_state(state_id: int, admin: AdminUser, session: DbSession) -> None:
    state = await session.get(TicketState, state_id)
    if state is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="State not found")
    state.valid_id = 2
    state.change_time = now()
    state.change_by = admin.id
    await invalidate_cache_for_state(session, state_id)
    await session.commit()
