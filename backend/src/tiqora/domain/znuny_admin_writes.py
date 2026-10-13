"""Znuny-compatible row creation shared by the admin routes and the ITSM setup.

Each helper validates, inserts, queues the Znuny cache invalidation and **flushes**; the caller
(route or setup) commits. They take exactly the inputs of the admin POST routes: no plugin logic,
no HTTP errors (``ValueError`` for invalid input, ``RuntimeError`` when the insert cannot be read
back).
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.legacy.dynamic_field import DynamicField
from tiqora.db.legacy.ticket import TicketState, TicketType
from tiqora.domain.dynamic_field_config import (
    config_to_yaml,
    validate_dynamic_field_config,
    znuny_config_keys,
)
from tiqora.znuny.cache_invalidation import invalidate_cache_type

# Types.pm / State.pm / DynamicField.pm: ``$Self->{CacheType}`` (+ DynamicFieldValue).
TYPE_CACHE_TYPES: tuple[str, ...] = ("Type",)
STATE_CACHE_TYPES: tuple[str, ...] = ("State",)
DYNAMIC_FIELD_CACHE_TYPES: tuple[str, ...] = ("DynamicField", "DynamicFieldValue")


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def _invalidate(session: AsyncSession, cache_types: Sequence[str]) -> None:
    for cache_type in cache_types:
        await invalidate_cache_type(session, cache_type)


async def create_ticket_type_row(
    session: AsyncSession, *, name: str, valid_id: int, user_id: int
) -> TicketType:
    ts = _now()
    row = TicketType(
        name=name,
        valid_id=valid_id,
        create_time=ts,
        create_by=user_id,
        change_time=ts,
        change_by=user_id,
    )
    session.add(row)
    await _invalidate(session, TYPE_CACHE_TYPES)
    await session.flush()
    return row


async def create_ticket_state_row(
    session: AsyncSession,
    *,
    name: str,
    type_id: int,
    comments: str | None,
    valid_id: int,
    user_id: int,
) -> TicketState:
    from tiqora.db.legacy.profile import default_color_for_write, insert_row_with_color

    ts = _now()
    color = default_color_for_write()
    if color is None:
        state = TicketState(
            name=name,
            type_id=type_id,
            comments=comments,
            valid_id=valid_id,
            create_time=ts,
            create_by=user_id,
            change_time=ts,
            change_by=user_id,
        )
        session.add(state)
    else:
        # Znuny 7.0+: ticket_state.color is NOT NULL and not on the 6.5 ORM baseline.
        state_id = await insert_row_with_color(
            session,
            table_name="ticket_state",
            values={
                "name": name,
                "type_id": type_id,
                "comments": comments,
                "valid_id": valid_id,
                "create_time": ts,
                "create_by": user_id,
                "change_time": ts,
                "change_by": user_id,
                "color": color,
            },
        )
        loaded = await session.get(TicketState, state_id)
        if loaded is None:
            raise RuntimeError("State insert failed")
        state = loaded
    await _invalidate(session, STATE_CACHE_TYPES)
    await session.flush()
    return state


async def create_dynamic_field_row(
    session: AsyncSession,
    *,
    name: str,
    label: str,
    field_type: str,
    object_type: str,
    config: dict[str, Any],
    field_order: int,
    valid_id: int = 1,
    internal_field: int = 0,
    user_id: int,
    caller_checked_keys: Collection[str] = (),
) -> DynamicField:
    """Validate *config* (final, as stored), serialise it to YAML and insert the field.

    ``caller_checked_keys``: top-level keys the caller has validated itself (e.g. the admin
    route's request-shaped extras, R14). They are stored as given and left out of the Znuny
    key check; every other key is validated as usual. The default checks every key. They
    must be keys Znuny does not know for *field_type*: naming one raises ``ValueError``
    (P2A-PM-I1), so the escape hatch can never skip Znuny's own checks.
    """
    skip = frozenset(caller_checked_keys)
    overlap = skip & znuny_config_keys(field_type)
    if overlap:
        raise ValueError(f"caller_checked_keys must not name Znuny config keys: {sorted(overlap)}")
    validate_dynamic_field_config(
        field_type, {k: v for k, v in config.items() if k not in skip} if skip else config
    )
    ts = _now()
    row = DynamicField(
        internal_field=internal_field,
        name=name,
        label=label,
        field_order=field_order,
        field_type=field_type,
        object_type=object_type,
        config=config_to_yaml(config),
        valid_id=valid_id,
        create_time=ts,
        create_by=user_id,
        change_time=ts,
        change_by=user_id,
    )
    session.add(row)
    await _invalidate(session, DYNAMIC_FIELD_CACHE_TYPES)
    await session.flush()
    return row
