"""Admin CRUD for Znuny PostMaster filters (``postmaster_filter`` table).

A named filter is a group of Match rows + Set rows sharing ``f_name``, with
``f_stop`` (StopAfterMatch) stored on every row for that name. Hard-delete is
used — the table has no ``valid_id`` and Znuny's ``FilterDelete`` also DELETEs.

Znuny ``Kernel::System::PostMaster::Filter`` reads the table with direct SQL
and does **not** declare a CacheType, so no cache invalidation is required.
"""

from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import delete, select

from tiqora.api.deps import DbSession
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.api.v1.admin.schemas import (
    PostmasterFilterOut,
    PostmasterFilterRuleOut,
    PostmasterFilterWrite,
)
from tiqora.db.legacy.config import PostmasterFilter

router = APIRouter(prefix="/postmaster-filters", tags=["admin:postmaster-filters"])

# Znuny Filter.pm FilterAdd uses these exact f_type strings.
_F_TYPE_MATCH = "Match"
_F_TYPE_SET = "Set"


def _rules_from_rows(rows: list[PostmasterFilter]) -> list[PostmasterFilterRuleOut]:
    return [
        PostmasterFilterRuleOut(
            f_name=r.f_name,
            f_stop=r.f_stop,
            f_type=r.f_type,
            f_key=r.f_key,
            f_value=r.f_value,
            f_not=r.f_not,
        )
        for r in rows
    ]


def _out_from_rows(name: str, rows: list[PostmasterFilter]) -> PostmasterFilterOut:
    return PostmasterFilterOut(name=name, rules=_rules_from_rows(rows))


async def _rows_for_name(session: DbSession, name: str) -> list[PostmasterFilter]:
    result = await session.execute(
        select(PostmasterFilter)
        .where(PostmasterFilter.f_name == name)
        .order_by(PostmasterFilter.f_type, PostmasterFilter.f_key, PostmasterFilter.f_value)
    )
    return list(result.scalars().all())


async def _name_exists(session: DbSession, name: str) -> bool:
    result = await session.execute(
        select(PostmasterFilter.f_name).where(PostmasterFilter.f_name == name).limit(1)
    )
    return result.scalar_one_or_none() is not None


def _build_rows(body: PostmasterFilterWrite) -> list[PostmasterFilter]:
    """Expand the write body into one ORM row per Match/Set entry."""
    f_stop = 1 if body.stop else 0
    rows: list[PostmasterFilter] = []
    seen: set[tuple[str, str, str]] = set()

    for match in body.match:
        key = match.key.strip()
        value = match.value
        if not key:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Match key must not be empty",
            )
        pk = (_F_TYPE_MATCH, key, value)
        if pk in seen:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Duplicate Match rule: {key}={value!r}",
            )
        seen.add(pk)
        rows.append(
            PostmasterFilter(
                f_name=body.name,
                f_stop=f_stop,
                f_type=_F_TYPE_MATCH,
                f_key=key,
                f_value=value,
                f_not=1 if match.negate else 0,
            )
        )

    for set_rule in body.set:
        key = set_rule.key.strip()
        value = set_rule.value
        if not key:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Set key must not be empty",
            )
        pk = (_F_TYPE_SET, key, value)
        if pk in seen:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Duplicate Set rule: {key}={value!r}",
            )
        seen.add(pk)
        rows.append(
            PostmasterFilter(
                f_name=body.name,
                f_stop=f_stop,
                f_type=_F_TYPE_SET,
                f_key=key,
                f_value=value,
                f_not=None,
            )
        )

    return rows


async def _delete_name(session: DbSession, name: str) -> None:
    await session.execute(delete(PostmasterFilter).where(PostmasterFilter.f_name == name))


@router.get("", response_model=list[PostmasterFilterOut])
async def list_postmaster_filters(
    admin: AdminUser, session: DbSession
) -> list[PostmasterFilterOut]:
    _ = admin
    result = await session.execute(
        select(PostmasterFilter).order_by(
            PostmasterFilter.f_name,
            PostmasterFilter.f_type,
            PostmasterFilter.f_key,
            PostmasterFilter.f_value,
        )
    )
    grouped: dict[str, list[PostmasterFilterRuleOut]] = defaultdict(list)
    for row in result.scalars().all():
        grouped[row.f_name].append(
            PostmasterFilterRuleOut(
                f_name=row.f_name,
                f_stop=row.f_stop,
                f_type=row.f_type,
                f_key=row.f_key,
                f_value=row.f_value,
                f_not=row.f_not,
            )
        )
    return [PostmasterFilterOut(name=name, rules=rules) for name, rules in grouped.items()]


@router.get("/{filter_name}", response_model=PostmasterFilterOut)
async def get_postmaster_filter(
    filter_name: str, admin: AdminUser, session: DbSession
) -> PostmasterFilterOut:
    _ = admin
    rows = await _rows_for_name(session, filter_name)
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filter not found")
    return _out_from_rows(filter_name, rows)


@router.post("", response_model=PostmasterFilterOut, status_code=status.HTTP_201_CREATED)
async def create_postmaster_filter(
    body: PostmasterFilterWrite, admin: AdminUser, session: DbSession
) -> PostmasterFilterOut:
    _ = admin
    if await _name_exists(session, body.name):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"PostMaster filter {body.name!r} already exists",
        )
    rows = _build_rows(body)
    session.add_all(rows)
    await session.commit()
    return _out_from_rows(body.name, await _rows_for_name(session, body.name))


@router.put("/{filter_name}", response_model=PostmasterFilterOut)
async def update_postmaster_filter(
    filter_name: str,
    body: PostmasterFilterWrite,
    admin: AdminUser,
    session: DbSession,
) -> PostmasterFilterOut:
    """Replace all rows for *filter_name*. Body ``name`` may rename the filter."""
    _ = admin
    existing = await _rows_for_name(session, filter_name)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filter not found")

    new_name = body.name
    if new_name != filter_name and await _name_exists(session, new_name):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"PostMaster filter {new_name!r} already exists",
        )

    # Single transaction: drop old name, insert replacement under body.name.
    await _delete_name(session, filter_name)
    # If renaming, also clear any residual rows under the new name (should be empty).
    if new_name != filter_name:
        await _delete_name(session, new_name)
    rows = _build_rows(body)
    session.add_all(rows)
    await session.commit()
    return _out_from_rows(new_name, await _rows_for_name(session, new_name))


@router.delete("/{filter_name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_postmaster_filter(filter_name: str, admin: AdminUser, session: DbSession) -> None:
    _ = admin
    existing = await _rows_for_name(session, filter_name)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filter not found")
    await _delete_name(session, filter_name)
    await session.commit()
