"""ITSM setup: the ticket types, the workaround state and the dynamic fields Tiqora's ITSM uses.

On a site without Znuny's ITSM packages these rows do not exist. The admin sees a preview
(``plan_setup``) and "Einrichten" (``apply_setup``) creates what is missing, with the rows the
Znuny packages themselves create (``seed_definitions``; names of the types, the state and the two
matrix fields follow the ``itsm`` settings section). Rules that keep this safe on a Mode A site:

* only ``missing`` items are created; an existing row is never changed (``present``, ``conflict``
  and ``invalid`` stay as they are and are reported);
* the rows are written by the admin write helpers (``domain.znuny_admin_writes``), so caches and
  defaults are exactly those of the admin screens;
* the caller commits (one transaction for the whole run).
"""

from __future__ import annotations

import copy
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final, Literal

from sqlalchemy import bindparam, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.legacy.dynamic_field import DynamicField
from tiqora.db.legacy.ticket import TicketState, TicketStateType, TicketType
from tiqora.domain.znuny_admin_writes import (
    create_dynamic_field_row,
    create_ticket_state_row,
    create_ticket_type_row,
)
from tiqora.itsm.seed_definitions import SeedDynamicField, SeedSet, for_profile
from tiqora.itsm.settings import ItsmSettings

SetupGroup = Literal["incident_problem", "change"]
SetupKind = Literal["ticket_type", "ticket_state", "dynamic_field"]
SetupStatus = Literal["present", "missing", "conflict", "invalid"]

SETUP_GROUPS: Final[tuple[SetupGroup, ...]] = ("incident_problem", "change")

# Both surveyed profiles ship identical rows (see ``seed_definitions``).
_PROFILE = "itsm-6"
_VALID = 1


@dataclass(frozen=True, slots=True)
class SetupItem:
    group: SetupGroup
    kind: SetupKind
    name: str
    #: conflict: same name, different type / field_type; invalid: present but valid_id != 1
    status: SetupStatus
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class _Want:
    group: SetupGroup
    kind: SetupKind
    name: str
    seed_field: SeedDynamicField | None = None
    state_type_name: str | None = None
    comments: str | None = None


def _wanted(settings: ItsmSettings, seed: SeedSet) -> list[_Want]:
    """Everything the setup would like to see, in creation order (deduplicated by name)."""
    wants: list[_Want] = []
    for name in (
        settings.incident_type,
        settings.major_incident_type,
        settings.service_request_type,
        settings.problem_type,
    ):
        wants.append(_Want("incident_problem", "ticket_type", name))
    state = seed.ticket_states[0]
    wants.append(
        _Want(
            "incident_problem",
            "ticket_state",
            settings.workaround_state,
            state_type_name=state.type_name,
            comments=state.comments,
        )
    )
    # The two matrix fields are configurable; the other fields keep Znuny's names.
    renamed = {"ITSMCriticality": settings.criticality_field, "ITSMImpact": settings.impact_field}
    for field in seed.dynamic_fields:
        wants.append(
            _Want(
                "incident_problem",
                "dynamic_field",
                renamed.get(field.name, field.name),
                seed_field=field,
            )
        )
    for name in (settings.change_type, settings.change_task_type):
        wants.append(_Want("change", "ticket_type", name))

    seen: set[tuple[str, str]] = set()
    unique: list[_Want] = []
    for want in wants:
        key = (want.kind, want.name.casefold())
        if key not in seen:
            seen.add(key)
            unique.append(want)
    return unique


async def _existing_types(session: AsyncSession, names: Iterable[str]) -> dict[str, TicketType]:
    rows = (await session.execute(select(TicketType).where(TicketType.name.in_(set(names))))).all()
    return {r[0].name.casefold(): r[0] for r in rows}


async def _existing_states(session: AsyncSession, names: Iterable[str]) -> dict[str, TicketState]:
    rows = (
        await session.execute(select(TicketState).where(TicketState.name.in_(set(names))))
    ).all()
    return {r[0].name.casefold(): r[0] for r in rows}


async def _existing_fields(session: AsyncSession, names: Iterable[str]) -> dict[str, DynamicField]:
    rows = (
        await session.execute(select(DynamicField).where(DynamicField.name.in_(set(names))))
    ).all()
    return {r[0].name.casefold(): r[0] for r in rows}


async def _state_type_ids(session: AsyncSession, names: Iterable[str]) -> dict[str, int]:
    wanted = set(names)
    if not wanted:
        return {}
    rows = (
        await session.execute(
            select(TicketStateType.id, TicketStateType.name).where(TicketStateType.name.in_(wanted))
        )
    ).all()
    return {str(name): int(type_id) for type_id, name in rows}


async def plan_setup(session: AsyncSession, settings: ItsmSettings) -> list[SetupItem]:
    """Preview: one item per row the ITSM needs. Reads only (separate statements, no JOIN)."""
    wants = _wanted(settings, for_profile(_PROFILE))
    types = await _existing_types(session, (w.name for w in wants if w.kind == "ticket_type"))
    states = await _existing_states(session, (w.name for w in wants if w.kind == "ticket_state"))
    fields = await _existing_fields(session, (w.name for w in wants if w.kind == "dynamic_field"))
    state_type_ids = await _state_type_ids(
        session, (w.state_type_name for w in wants if w.state_type_name)
    )
    state_type_names = {v: k for k, v in state_type_ids.items()}
    # Names of the state types of existing states (one more IN query, no JOIN).
    existing_state_type_ids = {s.type_id for s in states.values()}
    if existing_state_type_ids:
        rows = (
            await session.execute(
                select(TicketStateType.id, TicketStateType.name).where(
                    TicketStateType.id.in_(existing_state_type_ids)
                )
            )
        ).all()
        state_type_names.update({int(i): str(n) for i, n in rows})

    items: list[SetupItem] = []
    for want in wants:
        key = want.name.casefold()
        if want.kind == "ticket_type":
            row = types.get(key)
            items.append(_judge(want, row.valid_id if row else None))
        elif want.kind == "ticket_state":
            state = states.get(key)
            if state is None:
                if want.state_type_name not in state_type_ids:
                    items.append(
                        SetupItem(
                            want.group,
                            want.kind,
                            want.name,
                            "conflict",
                            f"state type {want.state_type_name!r} not found",
                        )
                    )
                else:
                    items.append(_judge(want, None))
            else:
                actual = state_type_names.get(state.type_id)
                if actual != want.state_type_name:
                    items.append(
                        SetupItem(
                            want.group,
                            want.kind,
                            want.name,
                            "conflict",
                            f"state type is {actual!r}, expected {want.state_type_name!r}",
                        )
                    )
                else:
                    items.append(_judge(want, state.valid_id))
        else:
            field = fields.get(key)
            seed_field = want.seed_field
            assert seed_field is not None  # noqa: S101  (dynamic_field wants always carry it)
            if field is None:
                items.append(_judge(want, None))
            elif field.field_type != seed_field.field_type or field.object_type != (
                seed_field.object_type
            ):
                items.append(
                    SetupItem(
                        want.group,
                        want.kind,
                        want.name,
                        "conflict",
                        f"existing field is {field.field_type}/{field.object_type}, "
                        f"expected {seed_field.field_type}/{seed_field.object_type}",
                    )
                )
            else:
                items.append(_judge(want, field.valid_id))
    return items


def _judge(want: _Want, valid_id: int | None) -> SetupItem:
    if valid_id is None:
        return SetupItem(want.group, want.kind, want.name, "missing")
    if valid_id != _VALID:
        return SetupItem(want.group, want.kind, want.name, "invalid", f"valid_id is {valid_id}")
    return SetupItem(want.group, want.kind, want.name, "present")


async def apply_setup(
    session: AsyncSession, settings: ItsmSettings, *, groups: set[str], user_id: int
) -> list[SetupItem]:
    """Create the ``missing`` items of the selected groups; the caller commits.

    Never changes a present, conflicting or invalid row. Returns the plan after the run.
    """
    unknown = groups - set(SETUP_GROUPS)
    if unknown:
        raise ValueError(f"unknown setup group(s): {sorted(unknown)}")
    seed = for_profile(_PROFILE)
    wants = {(w.kind, w.name.casefold()): w for w in _wanted(settings, seed)}
    items = await plan_setup(session, settings)
    missing = [i for i in items if i.status == "missing" and i.group in groups]

    state_type_ids = await _state_type_ids(
        session, (w.state_type_name for w in wants.values() if w.state_type_name)
    )
    order_start = await _next_field_order(session)
    for item in missing:
        want = wants[(item.kind, item.name.casefold())]
        if item.kind == "ticket_type":
            await create_ticket_type_row(session, name=item.name, valid_id=_VALID, user_id=user_id)
        elif item.kind == "ticket_state":
            assert want.state_type_name is not None  # noqa: S101
            await create_ticket_state_row(
                session,
                name=item.name,
                type_id=state_type_ids[want.state_type_name],
                comments=want.comments,
                valid_id=_VALID,
                user_id=user_id,
            )
        else:
            field = want.seed_field
            assert field is not None  # noqa: S101
            await create_dynamic_field_row(
                session,
                name=item.name,
                label=field.label,
                field_type=field.field_type,
                object_type=field.object_type,
                config=copy.deepcopy(field.config),
                field_order=order_start,
                valid_id=_VALID,
                internal_field=1,  # the Znuny ITSM packages mark their fields as internal
                user_id=user_id,
            )
            order_start += 1
    return await plan_setup(session, settings)


async def _next_field_order(session: AsyncSession) -> int:
    from sqlalchemy import func

    top = (await session.execute(select(func.max(DynamicField.field_order)))).scalar_one_or_none()
    return int(top or 0) + 1


async def setup_complete(session: AsyncSession, cfg: ItsmSettings) -> bool:
    """The types, the workaround state and the two matrix fields exist and are valid."""

    async def present(table: str, names: set[str]) -> bool:
        stmt = text(f"SELECT name FROM {table} WHERE valid_id = 1 AND name IN :n").bindparams(  # noqa: S608
            bindparam("n", expanding=True)
        )
        found = {str(r[0]) for r in (await session.execute(stmt, {"n": sorted(names)})).all()}
        return names <= found

    types = {cfg.incident_type, cfg.major_incident_type, cfg.service_request_type, cfg.problem_type}
    return (
        await present("ticket_type", types)
        and await present("ticket_state", {cfg.workaround_state})
        and await present("dynamic_field", {cfg.criticality_field, cfg.impact_field})
    )
