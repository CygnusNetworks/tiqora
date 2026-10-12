"""Saved CI searches: private by default, optionally shared; only the owner changes them."""

from __future__ import annotations

import json
import logging

from pydantic import ValidationError
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.cmdb.errors import CmdbForbidden, CmdbNotFound
from tiqora.cmdb.schemas import CiSearchCriteria, CmdbSavedSearchIn, CmdbSavedSearchOut
from tiqora.db.tiqora.models import TiqoraCmdbSavedSearch
from tiqora.jobs.service import utc_now

logger = logging.getLogger(__name__)


def dump_criteria(criteria: CiSearchCriteria) -> str:
    """The stored JSON (the filter's ``not`` key is an alias, hence ``by_alias``)."""
    return json.dumps(
        criteria.model_dump(mode="json", by_alias=True, exclude_none=True),
        separators=(",", ":"),
        ensure_ascii=False,
    )


def load_criteria(raw: str) -> CiSearchCriteria:
    """Broken or outdated stored JSON reads as empty criteria (a saved search never errors)."""
    try:
        return CiSearchCriteria.model_validate_json(raw)
    except (ValidationError, ValueError):
        logger.warning("cmdb: stored saved-search criteria are not valid, reading them as empty")
        return CiSearchCriteria()


def saved_to_out(row: TiqoraCmdbSavedSearch, user_id: int) -> CmdbSavedSearchOut:
    return CmdbSavedSearchOut(
        id=int(row.id),
        name=row.name,
        shared=bool(row.shared),
        owned=int(row.owner_user_id) == user_id,
        owner_user_id=int(row.owner_user_id),
        criteria=load_criteria(row.criteria),
        created_at=row.created_at,
        changed_at=row.changed_at,
    )


async def list_saved(session: AsyncSession, user_id: int) -> list[TiqoraCmdbSavedSearch]:
    """The user's own searches and everybody's shared ones, own first, then by name."""
    rows = (
        (
            await session.execute(
                select(TiqoraCmdbSavedSearch).where(
                    or_(
                        TiqoraCmdbSavedSearch.owner_user_id == user_id,
                        TiqoraCmdbSavedSearch.shared.is_(True),
                    )
                )
            )
        )
        .scalars()
        .all()
    )
    return sorted(
        rows, key=lambda r: (int(r.owner_user_id) != user_id, r.name.casefold(), int(r.id))
    )


async def create_saved(
    session: AsyncSession, user_id: int, data: CmdbSavedSearchIn
) -> TiqoraCmdbSavedSearch:
    now = utc_now()
    row = TiqoraCmdbSavedSearch(
        owner_user_id=user_id,
        name=data.name.strip(),
        criteria=dump_criteria(data.criteria),
        shared=data.shared,
        created_at=now,
        changed_at=now,
    )
    session.add(row)
    await session.flush()
    return row


async def _owned(session: AsyncSession, user_id: int, saved_id: int) -> TiqoraCmdbSavedSearch:
    """A search nobody may see (somebody else's private one) is "not found"; a shared one of
    somebody else is visible but not changeable (``CmdbForbidden``)."""
    row = await session.get(TiqoraCmdbSavedSearch, saved_id)
    if row is None:
        raise CmdbNotFound(f"saved search {saved_id}")
    if int(row.owner_user_id) != user_id:
        if not row.shared:
            raise CmdbNotFound(f"saved search {saved_id}")
        raise CmdbForbidden(f"saved search {saved_id}")
    return row


async def update_saved(
    session: AsyncSession, user_id: int, saved_id: int, data: CmdbSavedSearchIn
) -> TiqoraCmdbSavedSearch:
    row = await _owned(session, user_id, saved_id)
    row.name = data.name.strip()
    row.criteria = dump_criteria(data.criteria)
    row.shared = data.shared
    row.changed_at = utc_now()
    await session.flush()
    return row


async def delete_saved(session: AsyncSession, user_id: int, saved_id: int) -> None:
    row = await _owned(session, user_id, saved_id)
    await session.delete(row)
    await session.flush()


__all__ = [
    "create_saved",
    "delete_saved",
    "dump_criteria",
    "list_saved",
    "load_criteria",
    "saved_to_out",
    "update_saved",
]
