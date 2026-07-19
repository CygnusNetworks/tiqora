"""Admin CRUD for outbound webhook subscriptions (Phase 3c)."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from tiqora.api.deps import DbSession
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.api.v1.admin.schemas import WebhookCreate, WebhookOut, WebhookUpdate
from tiqora.db.tiqora.models import TiqoraWebhook

router = APIRouter(prefix="/webhooks", tags=["admin:webhooks"])


def _to_out(row: TiqoraWebhook) -> WebhookOut:
    try:
        events = json.loads(row.events) if row.events else []
    except (ValueError, TypeError):
        events = []
    return WebhookOut(
        id=row.id,
        name=row.name,
        url=row.url,
        events=events,
        valid=row.valid,
        created=row.created,
        changed=row.changed,
    )


@router.get("", response_model=list[WebhookOut])
async def list_webhooks(admin: AdminUser, session: DbSession) -> list[WebhookOut]:
    _ = admin
    result = await session.execute(select(TiqoraWebhook).order_by(TiqoraWebhook.name))
    return [_to_out(row) for row in result.scalars().all()]


@router.get("/{webhook_id}", response_model=WebhookOut)
async def get_webhook(webhook_id: int, admin: AdminUser, session: DbSession) -> WebhookOut:
    _ = admin
    row = await session.get(TiqoraWebhook, webhook_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found")
    return _to_out(row)


@router.post("", response_model=WebhookOut, status_code=status.HTTP_201_CREATED)
async def create_webhook(body: WebhookCreate, admin: AdminUser, session: DbSession) -> WebhookOut:
    _ = admin
    row = TiqoraWebhook(
        name=body.name,
        url=body.url,
        secret=body.secret,
        events=json.dumps(body.events),
        valid=body.valid,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _to_out(row)


@router.patch("/{webhook_id}", response_model=WebhookOut)
async def update_webhook(
    webhook_id: int, body: WebhookUpdate, admin: AdminUser, session: DbSession
) -> WebhookOut:
    _ = admin
    row = await session.get(TiqoraWebhook, webhook_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found")
    updates = body.model_dump(exclude_unset=True)
    if "events" in updates:
        updates["events"] = json.dumps(updates["events"])
    for field, value in updates.items():
        setattr(row, field, value)
    await session.commit()
    await session.refresh(row)
    return _to_out(row)


@router.delete("/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_webhook(webhook_id: int, admin: AdminUser, session: DbSession) -> None:
    """Soft-deactivate (``valid = False``); matches the invariant used by
    other admin CRUD resources (queues, states, ...) that are never hard-deleted."""
    row = await session.get(TiqoraWebhook, webhook_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found")
    row.valid = False
    await session.commit()
