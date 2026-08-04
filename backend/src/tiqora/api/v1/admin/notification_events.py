"""Admin CRUD for notification events (Znuny ``notification_event*`` tables)."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import text

from tiqora.api.deps import DbSession
from tiqora.api.v1.admin.common import (
    NOTIFICATION_EVENT_CACHE_TYPES,
    invalidate_znuny_cache_types,
    now,
)
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.api.v1.admin.schemas import (
    NotificationEventOut,
    NotificationEventUpdate,
    NotificationEventWrite,
    NotificationMessageIn,
)

router = APIRouter(prefix="/notification-events", tags=["admin:notification-events"])


async def _load_items(session: DbSession, notification_id: int) -> dict[str, list[str]]:
    rows = (
        await session.execute(
            text(
                "SELECT event_key, event_value FROM notification_event_item"
                " WHERE notification_id = :nid ORDER BY event_key, event_value"
            ),
            {"nid": notification_id},
        )
    ).all()
    out: dict[str, list[str]] = defaultdict(list)
    for key, value in rows:
        out[str(key)].append(str(value or ""))
    return dict(out)


async def _load_messages(session: DbSession, notification_id: int) -> list[NotificationMessageIn]:
    rows = (
        await session.execute(
            text(
                "SELECT language, subject, text, content_type FROM notification_event_message"
                " WHERE notification_id = :nid ORDER BY language"
            ),
            {"nid": notification_id},
        )
    ).all()
    return [
        NotificationMessageIn(
            language=str(r[0]),
            subject=str(r[1]),
            text=str(r[2]),
            content_type=str(r[3] or "text/plain"),
        )
        for r in rows
    ]


async def _to_out(session: DbSession, row: Any) -> NotificationEventOut:
    nid = int(row["id"])
    return NotificationEventOut(
        id=nid,
        name=str(row["name"]),
        comments=row["comments"],
        valid_id=int(row["valid_id"]),
        create_time=row["create_time"],
        change_time=row["change_time"],
        items=await _load_items(session, nid),
        messages=await _load_messages(session, nid),
    )


async def _replace_items(
    session: DbSession, notification_id: int, items: dict[str, list[str]]
) -> None:
    await session.execute(
        text("DELETE FROM notification_event_item WHERE notification_id = :nid"),
        {"nid": notification_id},
    )
    for key, values in items.items():
        for value in values:
            await session.execute(
                text(
                    "INSERT INTO notification_event_item (notification_id, event_key, event_value)"
                    " VALUES (:nid, :k, :v)"
                ),
                {"nid": notification_id, "k": key, "v": value},
            )


async def _replace_messages(
    session: DbSession, notification_id: int, messages: list[NotificationMessageIn]
) -> None:
    await session.execute(
        text("DELETE FROM notification_event_message WHERE notification_id = :nid"),
        {"nid": notification_id},
    )
    for msg in messages:
        await session.execute(
            text(
                "INSERT INTO notification_event_message"
                " (notification_id, subject, text, content_type, language)"
                " VALUES (:nid, :subj, :txt, :ct, :lang)"
            ),
            {
                "nid": notification_id,
                "subj": msg.subject,
                "txt": msg.text,
                "ct": msg.content_type,
                "lang": msg.language,
            },
        )


@router.get("", response_model=list[NotificationEventOut])
async def list_notification_events(
    admin: AdminUser, session: DbSession
) -> list[NotificationEventOut]:
    _ = admin
    rows = (
        await session.execute(
            text(
                "SELECT id, name, comments, valid_id, create_time, change_time"
                " FROM notification_event ORDER BY name"
            )
        )
    ).mappings().all()
    return [await _to_out(session, r) for r in rows]


@router.get("/{notification_id}", response_model=NotificationEventOut)
async def get_notification_event(
    notification_id: int, admin: AdminUser, session: DbSession
) -> NotificationEventOut:
    _ = admin
    row = (
        await session.execute(
            text(
                "SELECT id, name, comments, valid_id, create_time, change_time"
                " FROM notification_event WHERE id = :id"
            ),
            {"id": notification_id},
        )
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")
    return await _to_out(session, row)


@router.post("", response_model=NotificationEventOut, status_code=status.HTTP_201_CREATED)
async def create_notification_event(
    body: NotificationEventWrite, admin: AdminUser, session: DbSession
) -> NotificationEventOut:
    ts = now()
    result = await session.execute(
        text(
            "INSERT INTO notification_event"
            " (name, valid_id, comments, create_time, create_by, change_time, change_by)"
            " VALUES (:name, :valid, :comments, :ts, :uid, :ts, :uid)"
        ),
        {
            "name": body.name,
            "valid": body.valid_id,
            "comments": body.comments,
            "ts": ts,
            "uid": admin.id,
        },
    )
    # dialect-specific lastrowid
    nid = int(getattr(result, "lastrowid", 0) or 0)
    if not nid:
        nid = int(
            (
                await session.execute(
                    text(
                        "SELECT id FROM notification_event"
                        " WHERE name = :n ORDER BY id DESC LIMIT 1"
                    ),
                    {"n": body.name},
                )
            ).scalar_one()
        )
    await _replace_items(session, nid, body.items)
    await _replace_messages(session, nid, body.messages)
    await invalidate_znuny_cache_types(session, NOTIFICATION_EVENT_CACHE_TYPES)
    await session.commit()
    return await get_notification_event(nid, admin, session)


@router.patch("/{notification_id}", response_model=NotificationEventOut)
async def update_notification_event(
    notification_id: int,
    body: NotificationEventUpdate,
    admin: AdminUser,
    session: DbSession,
) -> NotificationEventOut:
    existing = (
        await session.execute(
            text("SELECT id FROM notification_event WHERE id = :id"),
            {"id": notification_id},
        )
    ).first()
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")

    data = body.model_dump(exclude_unset=True)
    items = data.pop("items", None)
    messages = data.pop("messages", None)
    sets: list[str] = []
    params: dict[str, Any] = {"id": notification_id, "uid": admin.id, "ts": now()}
    for col in ("name", "comments", "valid_id"):
        if col in data:
            sets.append(f"{col} = :{col}")
            params[col] = data[col]
    sets.append("change_time = :ts")
    sets.append("change_by = :uid")
    await session.execute(
        text(f"UPDATE notification_event SET {', '.join(sets)} WHERE id = :id"),
        params,
    )
    if items is not None:
        await _replace_items(session, notification_id, items)
    if messages is not None:
        await _replace_messages(session, notification_id, messages)
    await invalidate_znuny_cache_types(session, NOTIFICATION_EVENT_CACHE_TYPES)
    await session.commit()
    return await get_notification_event(notification_id, admin, session)


@router.delete("/{notification_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_notification_event(
    notification_id: int, admin: AdminUser, session: DbSession
) -> None:
    existing = (
        await session.execute(
            text("SELECT id FROM notification_event WHERE id = :id"),
            {"id": notification_id},
        )
    ).first()
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")
    await session.execute(
        text(
            "UPDATE notification_event SET valid_id = 2, change_time = :ts, change_by = :uid"
            " WHERE id = :id"
        ),
        {"id": notification_id, "ts": now(), "uid": admin.id},
    )
    await invalidate_znuny_cache_types(session, NOTIFICATION_EVENT_CACHE_TYPES)
    await session.commit()
