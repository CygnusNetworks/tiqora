"""Stable IdP-subject -> local-account links (D7).

Subjects are compared by ``subject_hash`` (sha256 hex), never by the collated
``subject`` column. ``user_id`` is a soft reference (Znuny tables are not touched
by DDL). Timestamps are app-written naive UTC.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.tiqora.models import TiqoraIdentityLink
from tiqora.identity.types import UserKind, subject_hash, utc_now


class LinkConflictError(Exception):
    """The subject is already linked to a different account; links are never re-pointed."""


@dataclass(frozen=True, slots=True)
class IdentityLink:
    id: int
    provider_id: int
    user_kind: UserKind
    subject: str
    user_id: int
    user_login: str
    first_seen: datetime
    last_seen: datetime


def _to_link(row: TiqoraIdentityLink) -> IdentityLink:
    return IdentityLink(
        id=row.id,
        provider_id=row.provider_id,
        user_kind="customer" if row.user_kind == "customer" else "agent",
        subject=row.subject,
        user_id=row.user_id,
        user_login=row.user_login,
        first_seen=row.first_seen,
        last_seen=row.last_seen,
    )


async def _get_row(
    session: AsyncSession, *, provider_id: int, user_kind: UserKind, subject: str
) -> TiqoraIdentityLink | None:
    result = await session.execute(
        select(TiqoraIdentityLink).where(
            TiqoraIdentityLink.provider_id == provider_id,
            TiqoraIdentityLink.user_kind == user_kind,
            TiqoraIdentityLink.subject_hash == subject_hash(subject),
        )
    )
    return result.scalar_one_or_none()


async def find_link(
    session: AsyncSession, *, provider_id: int, user_kind: UserKind, subject: str
) -> IdentityLink | None:
    row = await _get_row(session, provider_id=provider_id, user_kind=user_kind, subject=subject)
    return _to_link(row) if row is not None else None


async def upsert_link(
    session: AsyncSession,
    *,
    provider_id: int,
    user_kind: UserKind,
    subject: str,
    user_id: int,
    user_login: str,
) -> IdentityLink:
    """Insert, or refresh ``user_login``/``last_seen`` of the existing link. Flush only.

    An existing link is never re-pointed: a different ``user_id`` raises
    :class:`LinkConflictError`. A concurrent first login (unique violation on insert)
    is re-read and follows the same same-user/conflict rule.
    """
    now = utc_now()
    row = await _get_row(session, provider_id=provider_id, user_kind=user_kind, subject=subject)
    if row is None:
        new_row = TiqoraIdentityLink(
            provider_id=provider_id,
            user_kind=user_kind,
            subject_hash=subject_hash(subject),
            subject=subject[:512],
            user_id=user_id,
            user_login=user_login[:200],
            first_seen=now,
            last_seen=now,
        )
        try:
            async with session.begin_nested():
                session.add(new_row)
                await session.flush()
        except IntegrityError:
            row = await _get_row(
                session, provider_id=provider_id, user_kind=user_kind, subject=subject
            )
            if row is None:
                raise
        else:
            return _to_link(new_row)
    if row.user_id != user_id:
        raise LinkConflictError(subject_hash(subject))
    row.user_login = user_login[:200]
    row.last_seen = now
    await session.flush()
    return _to_link(row)


async def list_links(
    session: AsyncSession, *, provider_id: int, limit: int = 200
) -> list[IdentityLink]:
    result = await session.execute(
        select(TiqoraIdentityLink)
        .where(TiqoraIdentityLink.provider_id == provider_id)
        .order_by(TiqoraIdentityLink.last_seen.desc(), TiqoraIdentityLink.id.desc())
        .limit(limit)
    )
    return [_to_link(r) for r in result.scalars()]


async def delete_link(session: AsyncSession, *, provider_id: int, link_id: int) -> bool:
    result = await session.execute(
        delete(TiqoraIdentityLink).where(
            TiqoraIdentityLink.id == link_id, TiqoraIdentityLink.provider_id == provider_id
        )
    )
    return bool(getattr(result, "rowcount", 0))
