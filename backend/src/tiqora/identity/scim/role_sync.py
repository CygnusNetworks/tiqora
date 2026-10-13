"""SCIM-driven role sync: the pending queue, the ``scim:*`` pseudo attributes and the worker tick.

Import cycle (see login.py): ``identity.provisioning`` -> ``domain.user_provisioning`` ->
``tiqora.api.v1`` -> ``identity.http`` -> ``login`` -> ``store``. ``store.update_provider`` imports
this module inside the function, never at module level.
"""

from __future__ import annotations

import time
from collections.abc import Iterable

import structlog
from sqlalchemy import delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.config import get_settings
from tiqora.db.engine import get_session_factory
from tiqora.db.legacy.user import Users
from tiqora.db.tiqora.models import (
    TiqoraScimGroup,
    TiqoraScimGroupMember,
    TiqoraScimPendingSync,
    TiqoraScimUser,
)
from tiqora.domain.settings_store import KEY_SCIM_ROLE_SYNC_ENABLED, get_setting_bool
from tiqora.identity.mapping import (
    SCIM_GROUP_IDS_ATTR,
    SCIM_GROUPS_ATTR,
    diff_assignments,
    evaluate_rules,
    rules_use_scim,
)
from tiqora.identity.types import SYSTEM_USER_ID, utc_now

logger = structlog.get_logger(__name__)

_CHUNK = 1000
# One tick drains the queue for at most this long (the default interval is 15 s).
_TICK_BUDGET_SECONDS = 10.0


async def enqueue_role_sync(
    session: AsyncSession, provider_id: int, user_ids: Iterable[int]
) -> int:
    """Queue agents for a role re-evaluation; returns the number of rows written.

    Delete-then-bulk-insert with ``generation = max(old + 1, time.time_ns())`` (the tick deletes
    ``WHERE generation = <read>``, so a newer enqueue survives). The clock part keeps a row that
    was deleted and re-created, or two overlapping enqueues that both read the same old value,
    from repeating a generation a tick already read. Flushes only; the caller owns the
    transaction.
    """
    ids = sorted(set(user_ids))
    if not ids:
        return 0
    model = TiqoraScimPendingSync
    old: dict[int, int] = {}
    for i in range(0, len(ids), _CHUNK):
        chunk = ids[i : i + _CHUNK]
        rows = await session.execute(
            select(model.user_id, model.generation).where(
                model.provider_id == provider_id, model.user_id.in_(chunk)
            )
        )
        old.update({uid: gen for uid, gen in rows.all()})
        await session.execute(
            delete(model).where(model.provider_id == provider_id, model.user_id.in_(chunk))
        )
    now = utc_now()
    clock = time.time_ns()
    await session.execute(
        insert(model),
        [
            {
                "provider_id": provider_id,
                "user_id": uid,
                "queued_at": now,
                "generation": max(old.get(uid, 0) + 1, clock),
            }
            for uid in ids
        ],
    )
    await session.flush()
    return len(ids)


async def scim_attributes(
    session: AsyncSession, *, provider_id: int, user_id: int
) -> dict[str, tuple[str, ...]]:
    """The ``scim:*`` pseudo attributes of an agent: names and external ids of its groups.

    Both keys are always present (empty tuples without a live mapping or membership): "present but
    empty" means "no groups" for the wave-1 fail-safe in ``_plan_assignments``. Tiqora tables only.
    """
    names: set[str] = set()
    ext_ids: set[str] = set()
    member_id = (
        await session.execute(
            select(TiqoraScimUser.id).where(
                TiqoraScimUser.provider_id == provider_id,
                TiqoraScimUser.user_kind == "agent",
                TiqoraScimUser.user_id == user_id,
                TiqoraScimUser.deleted.is_(False),
            )
        )
    ).scalar_one_or_none()
    if member_id is not None:
        group_ids = (
            await session.scalars(
                select(TiqoraScimGroupMember.group_id).where(
                    TiqoraScimGroupMember.member_id == member_id
                )
            )
        ).all()
        for i in range(0, len(group_ids), _CHUNK):
            rows = await session.execute(
                select(TiqoraScimGroup.display_name, TiqoraScimGroup.external_id).where(
                    TiqoraScimGroup.provider_id == provider_id,
                    TiqoraScimGroup.id.in_(group_ids[i : i + _CHUNK]),
                )
            )
            for display_name, external_id in rows.all():
                names.add(display_name)
                if external_id:
                    ext_ids.add(external_id)
    return {
        SCIM_GROUPS_ATTR: tuple(sorted(names)),
        SCIM_GROUP_IDS_ATTR: tuple(sorted(ext_ids)),
    }


async def _sync_one(provider_id: int, user_id: int, generation: int) -> bool:
    """Re-evaluate one agent in its own transaction; True when applied, False when ignored.

    The pending row is deleted ``WHERE generation = <read>`` in the same transaction, so an
    enqueue that happened meanwhile survives for the next tick.
    """
    # Function-local: identity.provisioning -> ... -> login -> store (import cycle, see module doc).
    from tiqora.domain.user_provisioning import valid_group_ids, valid_role_ids
    from tiqora.identity.provisioning import apply_assignments, load_assignment_state
    from tiqora.identity.store import get_provider

    settings = get_settings()
    applied = False
    async with get_session_factory()() as session:
        provider = await get_provider(session, provider_id, secret_key=settings.secret_key)
        valid_user = (
            user_id != SYSTEM_USER_ID
            and (
                await session.execute(select(Users.valid_id).where(Users.id == user_id))
            ).scalar_one_or_none()
            == 1
        )
        if provider is not None and valid_user and rules_use_scim(provider.role_rules):
            attributes = await scim_attributes(session, provider_id=provider_id, user_id=user_id)
            mapped = (
                await session.execute(
                    select(TiqoraScimUser.id).where(
                        TiqoraScimUser.provider_id == provider_id,
                        TiqoraScimUser.user_kind == "agent",
                        TiqoraScimUser.user_id == user_id,
                        TiqoraScimUser.deleted.is_(False),
                    )
                )
            ).scalar_one_or_none()
            if mapped is not None:
                roles = await valid_role_ids(session)
                groups = await valid_group_ids(session)
                desired = evaluate_rules(
                    attributes, provider.role_rules, valid_role_ids=roles, valid_group_ids=groups
                )
                state = await load_assignment_state(
                    session, provider_id=provider_id, user_id=user_id
                )
                diff = diff_assignments(
                    desired,
                    state,
                    mode=provider.sync_mode,
                    valid_role_ids=roles,
                    valid_group_ids=groups,
                )
                await apply_assignments(
                    session,
                    provider_id=provider_id,
                    user_id=user_id,
                    diff=diff,
                    provider_slug=provider.slug,
                )
                applied = True
        model = TiqoraScimPendingSync
        await session.execute(
            delete(model).where(
                model.provider_id == provider_id,
                model.user_id == user_id,
                model.generation == generation,
            )
        )
        await session.commit()
    return applied


async def _push_back(provider_id: int, user_id: int, generation: int) -> None:
    """Move a failed row behind everything queued so far; it is retried, but not first."""
    model = TiqoraScimPendingSync
    try:
        async with get_session_factory()() as session:
            await session.execute(
                update(model)
                .where(
                    model.provider_id == provider_id,
                    model.user_id == user_id,
                    model.generation == generation,
                )
                .values(queued_at=utc_now())
            )
            await session.commit()
    except Exception:
        logger.exception(
            "scim_role_sync_push_back_failed", provider_id=provider_id, user_id=user_id
        )


async def run_scim_role_sync_tick(
    *, batch: int = 200, budget_seconds: float = _TICK_BUDGET_SECONDS
) -> dict[str, int]:
    """Apply the queued role re-evaluations (oldest first); ``{"skipped": 1}`` when switched off.

    One tick works through the queue in batches until it is empty or *budget_seconds* are used
    up, so a large rename or rules change does not wait one interval per batch. Each row is
    handled at most once per tick; a failed row is pushed behind the others (``queued_at``
    reset) so it cannot block the head of the queue.

    Counts: ``processed`` (applied), ``ignored`` (nothing to do: missing provider, system or
    invalid user, no live mapping, rules no longer SCIM-based; the row is dropped), ``errors``
    (the row stays and is retried next tick).
    """
    async with get_session_factory()() as session:
        if not await get_setting_bool(session, KEY_SCIM_ROLE_SYNC_ENABLED, True):
            return {"skipped": 1}
    model = TiqoraScimPendingSync
    deadline = time.monotonic() + budget_seconds
    seen: set[tuple[int, int]] = set()
    processed = ignored = errors = 0
    while time.monotonic() < deadline:
        async with get_session_factory()() as session:
            # Rows handled earlier in this tick may still be queued (failed, or re-queued with a
            # newer generation); read past them.
            fetched = (
                await session.execute(
                    select(model.provider_id, model.user_id, model.generation)
                    .order_by(model.queued_at, model.provider_id, model.user_id)
                    .limit(batch + len(seen))
                )
            ).all()
        rows = [r for r in fetched if (int(r[0]), int(r[1])) not in seen][:batch]
        if not rows:
            break
        for provider_id, user_id, generation in rows:
            key = (int(provider_id), int(user_id))
            seen.add(key)
            try:
                if await _sync_one(key[0], key[1], int(generation)):
                    processed += 1
                else:
                    ignored += 1
            except Exception:
                errors += 1
                logger.exception("scim_role_sync_failed", provider_id=provider_id, user_id=user_id)
                await _push_back(key[0], key[1], int(generation))
    if seen:
        logger.info("scim_role_sync_tick", processed=processed, ignored=ignored, errors=errors)
    return {"processed": processed, "ignored": ignored, "errors": errors}
