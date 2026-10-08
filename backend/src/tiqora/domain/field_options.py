"""ACL-filtered field options: attribute fields (queue, state, ...) and dynamic fields.

The id->name maps and ``collect_ticket_field_options`` used to live in
``api/v1/reference.py``; they moved here so :func:`collect_field_options` can
reuse them without an import cycle (``reference.py`` re-imports them).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.legacy.dynamic_field import DynamicField
from tiqora.db.legacy.queue import Queue, Service, Sla
from tiqora.db.legacy.ticket import TicketPriority, TicketState, TicketType
from tiqora.domain.auth import AuthenticatedUser
from tiqora.domain.ticket_acl import apply_ticket_acl
from tiqora.domain.ticket_service import TicketService
from tiqora.permissions.engine import PermissionEngine

# Znuny's "valid" list id -- 1 == valid, everything else is invalid/temporary.
_VALID = 1

ATTRIBUTE_OPTION_FIELDS: tuple[str, ...] = ("state", "queue", "priority", "type", "service", "sla")
DF_OPTION_PREFIX = "DynamicField_"
_OPTION_DF_TYPES = frozenset({"Dropdown", "Multiselect"})

_FIELD_TO_SUBTYPE: dict[str, str] = {
    "state": "State",
    "queue": "Queue",
    "priority": "Priority",
    "type": "Type",
    "service": "Service",
    "sla": "SLA",
}


class TicketFieldOptionsOut(BaseModel):
    """ACL-filtered id→name maps (string keys for JSON).

    Only requested fields are populated; others are omitted (empty dict).
    """

    state: dict[str, str] = {}
    queue: dict[str, str] = {}
    priority: dict[str, str] = {}
    type: dict[str, str] = {}
    service: dict[str, str] = {}
    sla: dict[str, str] = {}


async def _base_field_maps(
    session: AsyncSession,
    user: AuthenticatedUser,
    fields: set[str],
    *,
    queue_id: int | None = None,
) -> dict[str, dict[int, str]]:
    """Unfiltered valid id→name maps for the requested fields.

    Queues are still permission-gated (``ro``); ACL is additional filtering.
    """
    out: dict[str, dict[int, str]] = {}
    if "state" in fields:
        rows = (
            await session.execute(
                select(TicketState.id, TicketState.name)
                .where(TicketState.valid_id == _VALID)
                .order_by(TicketState.id)
            )
        ).all()
        out["state"] = {int(r[0]): r[1] for r in rows}
    if "priority" in fields:
        rows = (
            await session.execute(
                select(TicketPriority.id, TicketPriority.name)
                .where(TicketPriority.valid_id == _VALID)
                .order_by(TicketPriority.id)
            )
        ).all()
        out["priority"] = {int(r[0]): r[1] for r in rows}
    if "type" in fields:
        rows = (
            await session.execute(
                select(TicketType.id, TicketType.name)
                .where(TicketType.valid_id == _VALID)
                .order_by(TicketType.name)
            )
        ).all()
        out["type"] = {int(r[0]): r[1] for r in rows}
    if "service" in fields:
        rows = (
            await session.execute(
                select(Service.id, Service.name)
                .where(Service.valid_id == _VALID)
                .order_by(Service.name)
            )
        ).all()
        out["service"] = {int(r[0]): r[1] for r in rows}
    if "sla" in fields:
        rows = (
            await session.execute(
                select(Sla.id, Sla.name).where(Sla.valid_id == _VALID).order_by(Sla.name)
            )
        ).all()
        out["sla"] = {int(r[0]): r[1] for r in rows}
    if "queue" in fields:
        pe = PermissionEngine(session)
        group_ids = await pe.groups_for_permission(user.id, "ro")
        if not group_ids:
            out["queue"] = {}
        else:
            rows = (
                await session.execute(
                    select(Queue.id, Queue.name)
                    .where(Queue.group_id.in_(group_ids), Queue.valid_id == _VALID)
                    .order_by(Queue.name)
                )
            ).all()
            out["queue"] = {int(r[0]): r[1] for r in rows}
    _ = queue_id  # reserved for form-side Queue checks when callers pass it
    return out


async def ticket_acl_context(
    session: AsyncSession,
    *,
    queue_id: int | None = None,
    service_id: int | None = None,
    type_id: int | None = None,
    state_id: int | None = None,
    priority_id: int | None = None,
    sla_id: int | None = None,
) -> dict[str, Any]:
    """Form-side ``Ticket`` ACL check values (ids and names) for the given selections."""
    ticket_ctx: dict[str, Any] = {}
    if queue_id is not None:
        queue_row = await session.get(Queue, queue_id)
        ticket_ctx["QueueID"] = str(queue_id)
        if queue_row is not None:
            ticket_ctx["Queue"] = queue_row.name
    if service_id is not None:
        svc = await session.get(Service, service_id)
        ticket_ctx["ServiceID"] = str(service_id)
        if svc is not None:
            ticket_ctx["Service"] = svc.name
    if type_id is not None:
        ty = await session.get(TicketType, type_id)
        ticket_ctx["TypeID"] = str(type_id)
        if ty is not None:
            ticket_ctx["Type"] = ty.name
    if state_id is not None:
        st = await session.get(TicketState, state_id)
        ticket_ctx["StateID"] = str(state_id)
        if st is not None:
            ticket_ctx["State"] = st.name
    if priority_id is not None:
        pr = await session.get(TicketPriority, priority_id)
        ticket_ctx["PriorityID"] = str(priority_id)
        if pr is not None:
            ticket_ctx["Priority"] = pr.name
    if sla_id is not None:
        sla = await session.get(Sla, sla_id)
        ticket_ctx["SLAID"] = str(sla_id)
        if sla is not None:
            ticket_ctx["SLA"] = sla.name
    return ticket_ctx


async def collect_ticket_field_options(
    session: AsyncSession,
    user: AuthenticatedUser,
    *,
    fields: set[str],
    ticket_id: int | None = None,
    action: str | None = None,
    queue_id: int | None = None,
    service_id: int | None = None,
    type_id: int | None = None,
    state_id: int | None = None,
    priority_id: int | None = None,
    sla_id: int | None = None,
) -> TicketFieldOptionsOut:
    """Load base maps, apply Ticket ACL, then Ticket Attribute Relations."""
    from tiqora.domain.ticket_acl import filter_id_name_map
    from tiqora.domain.ticket_attribute_relations import apply_attribute_relations_to_field_maps

    base = await _base_field_maps(session, user, fields, queue_id=queue_id)
    ticket_ctx = await ticket_acl_context(
        session,
        queue_id=queue_id,
        service_id=service_id,
        type_id=type_id,
        state_id=state_id,
        priority_id=priority_id,
        sla_id=sla_id,
    )
    checks: dict[str, Any] | None = {"Ticket": dict(ticket_ctx)} if ticket_ctx else None

    acl_maps: dict[str, dict[int, str]] = {}
    for field, mapping in base.items():
        subtype = _FIELD_TO_SUBTYPE[field]
        filtered = await filter_id_name_map(
            session,
            user_id=user.id,
            items=mapping,
            return_sub_type=subtype,
            ticket_id=ticket_id,
            action=action,
            checks=checks,
        )
        acl_maps[field] = filtered

    if ticket_ctx:
        acl_maps = await apply_attribute_relations_to_field_maps(
            session, acl_maps, ticket_context=ticket_ctx
        )

    result = TicketFieldOptionsOut()
    for field, mapping in acl_maps.items():
        setattr(result, field, {str(k): v for k, v in mapping.items()})
    return result


class FieldOptionsContext(BaseModel):
    action: str = "AgentTicketZoom"  # Znuny frontend Action for ACL Properties
    ticket_id: int | None = None
    queue_id: int | None = None
    service_id: int | None = None
    type_id: int | None = None
    state_id: int | None = None
    priority_id: int | None = None
    sla_id: int | None = None
    customer_user_id: str | None = None
    #: Current form values by dynamic-field name (feed ``DynamicField_<name>`` ACL checks).
    dynamic_fields: dict[str, list[str]] = Field(default_factory=dict)


class FieldOptionsRequest(BaseModel):
    #: "queue", "state", ..., or "DynamicField_<Name>".
    fields: list[str] = Field(min_length=1, max_length=50)
    context: FieldOptionsContext = Field(default_factory=FieldOptionsContext)


class FieldOptionOut(BaseModel):
    value: str
    label: str


class FieldOptionsOut(BaseModel):
    #: Ordered lists; unknown and non-option fields are omitted.
    options: dict[str, list[FieldOptionOut]]


def _sorted_options(items: dict[str, str]) -> list[FieldOptionOut]:
    # Znuny LayoutObject->BuildSelection default (no Sort option): values sorted
    # case-insensitively, `sort { lc $a cmp lc $b } values %List`
    # (Kernel/Output/HTML/Layout.pm, _BuildSelectionDataRefCreate, ~l. 5538).
    # Ties (same label) fall back to the key so the order is deterministic.
    ordered = sorted(items.items(), key=lambda kv: (kv[1].lower(), kv[0]))
    return [FieldOptionOut(value=k, label=v) for k, v in ordered]


async def collect_field_options(
    session: AsyncSession, user: AuthenticatedUser, req: FieldOptionsRequest
) -> FieldOptionsOut:
    """Options for attribute and dynamic fields, filtered by Ticket ACLs.

    With ``context.ticket_id`` the ticket must be readable
    (:class:`~tiqora.domain.ticket_service.TicketNotFound` /
    :class:`~tiqora.domain.ticket_service.TicketAccessDenied` propagate); its
    attributes are the default ACL context, explicit ``context`` ids override.
    """
    from tiqora.domain.dynamic_field_config import config_from_yaml

    ctx = req.context
    ids: dict[str, int | None] = {
        "queue_id": ctx.queue_id,
        "service_id": ctx.service_id,
        "type_id": ctx.type_id,
        "state_id": ctx.state_id,
        "priority_id": ctx.priority_id,
        "sla_id": ctx.sla_id,
    }
    if ctx.ticket_id is not None:
        detail = await TicketService(session).get_ticket(user.id, ctx.ticket_id)
        for key in ids:
            if ids[key] is None:
                ids[key] = getattr(detail, key, None)

    attr_fields = [f.strip().lower() for f in req.fields]
    attr_requested = {f for f in attr_fields if f in ATTRIBUTE_OPTION_FIELDS}
    attribute_maps: dict[str, dict[str, str]] = {}
    if attr_requested:
        collected = await collect_ticket_field_options(
            session,
            user,
            fields=attr_requested,
            ticket_id=ctx.ticket_id,
            action=ctx.action,
            **ids,
        )
        attribute_maps = {f: getattr(collected, f) for f in attr_requested}

    checks: dict[str, Any] | None = None
    df_rows: dict[str, DynamicField] = {}
    df_names = [
        f[len(DF_OPTION_PREFIX) :]
        for f in req.fields
        if f.startswith(DF_OPTION_PREFIX) and f != DF_OPTION_PREFIX
    ]
    if df_names:
        rows = (
            (
                await session.execute(
                    select(DynamicField).where(
                        DynamicField.name.in_(df_names),
                        DynamicField.valid_id == _VALID,
                        DynamicField.object_type == "Ticket",
                        DynamicField.field_type.in_(sorted(_OPTION_DF_TYPES)),
                    )
                )
            )
            .scalars()
            .all()
        )
        # Exact-case match only (the IN above is collation-insensitive).
        df_rows = {r.name: r for r in rows if r.name in df_names}
        ticket_ctx = await ticket_acl_context(session, **ids)
        for name, values in ctx.dynamic_fields.items():
            ticket_ctx[f"{DF_OPTION_PREFIX}{name}"] = list(values)
        checks = {"Ticket": ticket_ctx} if ticket_ctx else None

    options: dict[str, list[FieldOptionOut]] = {}
    for requested in req.fields:
        key = requested.strip().lower()
        if key in ATTRIBUTE_OPTION_FIELDS:
            mapping = attribute_maps.get(key, {})
            options[key] = [FieldOptionOut(value=k, label=v) for k, v in mapping.items()]
            continue
        if not requested.startswith(DF_OPTION_PREFIX):
            continue
        name = requested[len(DF_OPTION_PREFIX) :]
        df = df_rows.get(name)
        if df is None:
            continue
        possible = config_from_yaml(df.config).get("PossibleValues")
        if not isinstance(possible, dict):
            continue
        labels = {str(k): str(v) for k, v in possible.items()}
        # Not filter_id_name_map: it casts the keys to int, DF keys are free text.
        # ACL rules name the stored keys, not the labels: like AgentTicketPhone.pm
        # ("convert possible values key => value to key => key for ACLs") the
        # data handed to the ACL is key => key; labels are mapped back afterwards.
        filtered = await apply_ticket_acl(
            session,
            user_id=user.id,
            data={k: k for k in labels},
            return_type="Ticket",
            return_sub_type=f"{DF_OPTION_PREFIX}{name}",
            ticket_id=ctx.ticket_id,
            action=ctx.action,
            checks=checks,
            customer_user_id=ctx.customer_user_id,
        )
        options[requested] = _sorted_options({k: labels[k] for k in filtered if k in labels})
    return FieldOptionsOut(options=options)
