"""Queue tree read endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from tiqora.api.deps import CurrentUser, DbSession
from tiqora.domain.queue_service import QueueService
from tiqora.domain.schemas import QueueNode

router = APIRouter(prefix="/queues", tags=["queues"])


@router.get("", response_model=list[QueueNode])
async def list_queues(user: CurrentUser, session: DbSession) -> list[QueueNode]:
    return await QueueService(session).list_queues(user.id)
