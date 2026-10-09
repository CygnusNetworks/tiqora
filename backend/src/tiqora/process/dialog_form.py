"""Server-built activity dialog forms: visible fields in order, with kind and ACL-filtered options.

The browser never has to interpret ``Display``/``Interface``/field aliases itself: it renders
what :func:`build_dialog_form` returns (plan D9). Znuny references: ``AgentTicketProcess.pm``
``_RenderQueue``/``_RenderState``/... (every option list is a ``TicketAcl`` call with
``Action => AgentTicketProcess``), ``_RenderLock`` (:4013, "no ACL checking yet"),
``CustomerTicketProcess.pm:75-82`` (skipped fields).

Option sources:

* agents -- wave-0 :func:`tiqora.domain.field_options.collect_field_options`, so the dialog
  filters exactly like the zoom pickers (``Owner``/``Responsible`` are ``{id: login}`` lists
  with ``ReturnSubType`` ``Owner``/``Responsible``, labels are the full names),
* customers -- :func:`collect_customer_field_options` (same tables, customer principal,
  ``Action => CustomerTicketProcess``).

Agent option lists get the dialog's process context (``ProcessEntityID``/``ActivityDialogEntityID``)
for live ``Process`` ACL checks. Owner/Responsible choices follow the queue group permissions like
Znuny's ``_GetOwners``/``_GetResponsibles`` (ticket queue; for start forms the dialog's default
Queue, else ``Process::DefaultQueue``; everyone with ``Ticket::ChangeOwnerToEveryone``).

Known limitations: the customer SLA list is the union over the customer's services
(the form has no selected service yet), and the customer Queue list keeps the system intake
queues (Postmaster/Raw/Junk) hidden on top of Znuny's create-group rule (Tiqora hardening).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.legacy.dynamic_field import DynamicField
from tiqora.db.legacy.queue import Queue, Service, ServiceCustomerUser, ServiceSla, Sla
from tiqora.db.legacy.ticket import Ticket
from tiqora.domain.dynamic_field_config import config_from_yaml
from tiqora.domain.field_options import (
    AGENT_OPTION_FIELDS,
    ATTRIBUTE_OPTION_FIELDS,
    DF_OPTION_PREFIX,
    FIELD_TO_SUBTYPE,
    VALID_ID,
    FieldOptionsContext,
    FieldOptionsRequest,
    base_field_maps,
    collect_field_options,
    sorted_options,
    ticket_acl_context,
)
from tiqora.domain.ticket_acl import apply_ticket_acl
from tiqora.domain.ticket_attribute_relations import apply_attribute_relations_to_field_maps
from tiqora.process.actor import ProcessActor
from tiqora.process.availability import (
    assert_can_start,
    assert_can_submit,
    customer_create_queue_ids,
)
from tiqora.process.config import ActivityDialogFieldConfig
from tiqora.process.exceptions import ProcessNotFound, TicketNotInProcess
from tiqora.process.fields import field_config, visible_fields
from tiqora.process.graph import ActivityDialogNode, ProcessGraph, ProcessRepository
from tiqora.process.schemas import (
    FieldKind,
    ProcessDialogFieldOut,
    ProcessDialogFormOut,
    ProcessDialogOptionOut,
)
from tiqora.process.ticket_state import get_ticket_process_candidates
from tiqora.znuny.sysconfig import SysConfig

if TYPE_CHECKING:
    from tiqora.domain.auth import AuthenticatedUser

__all__ = [
    "build_dialog_form",
    "build_start_form",
    "build_ticket_dialog_form",
    "collect_customer_field_options",
    "customer_service_map",
]

# Znuny's default customer TicketMoveList: every valid queue except the system queues.
_CUSTOMER_HIDDEN_QUEUES = frozenset({"Postmaster", "Raw", "Junk"})

_ATTRIBUTE_KEY = {
    "Queue": "queue",
    "State": "state",
    "Priority": "priority",
    "Type": "type",
    "Service": "service",
    "SLA": "sla",
    "Owner": "owner",
    "Responsible": "responsible",
}
_DF_KINDS: dict[str, FieldKind] = {
    "Text": "text",
    "TextArea": "textarea",
    "Dropdown": "select",
    "Multiselect": "multiselect",
    "Checkbox": "boolean",
    "Date": "date",
    "DateTime": "datetime",
}
_DF_OPTION_KINDS = frozenset({"select", "multiselect"})
_CUSTOMER_FIELDS = ("CustomerUserID", "CustomerID")


async def _load_dynamic_fields(
    session: AsyncSession, field_names: Sequence[str]
) -> dict[str, DynamicField]:
    """Valid ticket dynamic fields by name (``field_names`` carry the ``DynamicField_`` prefix)."""
    names = [n[len(DF_OPTION_PREFIX) :] for n in field_names if n.startswith(DF_OPTION_PREFIX)]
    if not names:
        return {}
    rows = (
        (
            await session.execute(
                select(DynamicField).where(
                    DynamicField.name.in_(names),
                    DynamicField.valid_id == VALID_ID,
                    DynamicField.object_type == "Ticket",
                )
            )
        )
        .scalars()
        .all()
    )
    # Exact-case match only (the IN above is collation-insensitive).
    return {f"{DF_OPTION_PREFIX}{r.name}": r for r in rows if r.name in names}


def _field_kind(name: str, dynamic_fields: Mapping[str, DynamicField]) -> FieldKind:
    if name in _ATTRIBUTE_KEY or name == "Lock":
        return "select"
    if name == "Title":
        return "text"
    if name == "Article":
        return "article"
    if name == "PendingTime":
        return "pending_time"
    if name == "CustomerUserID":
        return "customer"
    df = dynamic_fields.get(name)
    if df is not None:
        return _DF_KINDS.get(df.field_type, "unsupported")
    return "unsupported"  # Attachments (phase 2, rulings F34), unknown names, unknown DFs


async def _customer_queues(
    session: AsyncSession, actor: ProcessActor, sysconfig: SysConfig
) -> dict[int, str]:
    """The customer's create queues (:func:`customer_create_queue_ids`, Znuny ``_GetQueues``),
    minus Postmaster/Raw/Junk. Tiqora hardening, not Znuny behaviour: Znuny offers these queues
    when their group (``users``) is in ``CustomerGroupAlwaysGroups``; the portal never lists
    the intake queues. The server-side create check does not apply this exclusion."""
    queues = await customer_create_queue_ids(session, actor, sysconfig)
    return {i: n for i, n in queues.items() if n not in _CUSTOMER_HIDDEN_QUEUES}


async def customer_service_map(
    session: AsyncSession, login: str, sysconfig: SysConfig
) -> dict[int, str]:
    """``TicketServiceList`` for a customer user (``Ticket.pm:2503-2560``): the valid services of
    ``CustomerUserServiceMemberList`` (``Service.pm:1190-1235``; no VALID assigned service ->
    the ``<DEFAULT>`` services), without children of invalid services unless
    ``Ticket::Service::KeepChildren`` (``Service.pm:188-205``)."""
    all_rows = (await session.execute(select(Service.id, Service.name, Service.valid_id))).all()
    valid_ids = {int(r[0]) for r in all_rows if r[2] == VALID_ID}

    async def member_ids(who: str) -> set[int]:
        rows = await session.execute(
            select(ServiceCustomerUser.service_id).where(
                ServiceCustomerUser.customer_user_login == who
            )
        )
        return {int(r) for r in rows.scalars()} & valid_ids

    ids = await member_ids(login)
    if not ids:
        ids = await member_ids("<DEFAULT>")
    if not ids:
        return {}
    invalid_names = [r[1] for r in all_rows if r[2] != VALID_ID]
    keep_children = str(await sysconfig.get("Ticket::Service::KeepChildren", 0)) in (
        "1",
        "True",
        "true",
    )
    out: dict[int, str] = {}
    for sid, name, _valid in all_rows:
        if int(sid) not in ids:
            continue
        if not keep_children and any(name.startswith(f"{bad}::") for bad in invalid_names):
            continue
        out[int(sid)] = name
    return dict(sorted(out.items(), key=lambda kv: kv[1]))


async def _customer_slas(session: AsyncSession, services: Mapping[int, str]) -> dict[int, str]:
    """SLAs assigned to the customer's services (``_GetSLAs`` asks ``SLAList(ServiceID)`` for the
    chosen service; the form carries no selection yet, so the union is offered)."""
    if not services:
        return {}
    rows = (
        await session.execute(
            select(Sla.id, Sla.name)
            .join(ServiceSla, ServiceSla.sla_id == Sla.id)
            .where(ServiceSla.service_id.in_(sorted(services)), Sla.valid_id == VALID_ID)
            .order_by(Sla.name)
        )
    ).all()
    return {int(r[0]): r[1] for r in rows}


async def _ticket_attribute_ids(session: AsyncSession, ticket_id: int | None) -> dict[str, int]:
    """Current attribute ids of the ticket for the Ticket Attribute Relations context."""
    if ticket_id is None:
        return {}
    row = (
        await session.execute(
            select(
                Ticket.queue_id,
                Ticket.service_id,
                Ticket.type_id,
                Ticket.ticket_state_id,
                Ticket.ticket_priority_id,
                Ticket.sla_id,
            ).where(Ticket.id == ticket_id)
        )
    ).first()
    if row is None:
        return {}
    keys = ("queue_id", "service_id", "type_id", "state_id", "priority_id", "sla_id")
    return {k: int(v) for k, v in zip(keys, row, strict=True) if v is not None}


async def collect_customer_field_options(
    session: AsyncSession,
    actor: ProcessActor,
    fields: Sequence[str],
    ticket_id: int | None,
    *,
    process: Mapping[str, str] | None = None,
    dynamic_fields: Mapping[str, DynamicField] | None = None,
    queue_id: int | None = None,
) -> dict[str, list[ProcessDialogOptionOut]]:
    """Options for the customer interface, keyed by the requested field name.

    *fields* are ``queue``/``state``/``priority``/``type``/``service``/``sla`` and
    ``DynamicField_<Name>``; each list is filtered with the customer principal and
    ``Action => CustomerTicketProcess`` (*process*: ``ProcessEntityID`` /
    ``ActivityDialogEntityID`` for ``Process`` properties).
    """
    login = actor.customer_user_login
    if actor.interface != "CustomerInterface" or not login:
        raise ValueError("collect_customer_field_options needs a customer actor")
    sysconfig = SysConfig(session)
    out: dict[str, list[ProcessDialogOptionOut]] = {}

    attrs = {f for f in fields if f in ATTRIBUTE_OPTION_FIELDS and f not in AGENT_OPTION_FIELDS}
    base = await base_field_maps(session, None, attrs - {"queue", "service", "sla"})
    if "queue" in attrs:
        base["queue"] = await _customer_queues(session, actor, sysconfig)
    if attrs & {"service", "sla"}:
        # CustomerTicketProcess.pm:4569-4602 / 4548-4566: only the customer user's services.
        services = await customer_service_map(session, login, sysconfig)
        if "service" in attrs:
            base["service"] = services
        if "sla" in attrs:
            base["sla"] = await _customer_slas(session, services)
    acl_maps: dict[str, dict[int, str]] = {}
    for field in (f for f in fields if f in base):
        mapping = base[field]
        kept = await apply_ticket_acl(
            session,
            user_id=None,
            customer_user_login=login,
            data=dict(mapping),
            return_type="Ticket",
            return_sub_type=FIELD_TO_SUBTYPE[field],
            ticket_id=ticket_id,
            action=actor.frontend_action,
            process=process,
            sysconfig=sysconfig,
        )
        acl_maps[field] = {k: v for k, v in mapping.items() if k in kept}
    # Ticket Attribute Relations, like the agent source (collect_ticket_field_options): the
    # ticket's own attributes (or the dialog's default queue) are the context.
    ids = await _ticket_attribute_ids(session, ticket_id)
    if ticket_id is None and queue_id is not None:
        ids["queue_id"] = queue_id
    ticket_ctx = await ticket_acl_context(session, **ids)
    if ticket_ctx:
        acl_maps = await apply_attribute_relations_to_field_maps(
            session, acl_maps, ticket_context=ticket_ctx
        )
    for field, mapping in acl_maps.items():
        out[field] = [ProcessDialogOptionOut(value=str(k), label=v) for k, v in mapping.items()]

    df_names = [f for f in fields if f.startswith(DF_OPTION_PREFIX)]
    rows = (
        dynamic_fields
        if dynamic_fields is not None
        else await _load_dynamic_fields(session, df_names)
    )
    for name in df_names:
        df = rows.get(name)
        if df is None or _DF_KINDS.get(df.field_type) not in _DF_OPTION_KINDS:
            continue
        possible = config_from_yaml(df.config).get("PossibleValues")
        if not isinstance(possible, dict):
            continue
        labels = {str(k): str(v) for k, v in possible.items()}
        # Like the agent source: ACL rules name the stored keys, so hand them key => key.
        kept = await apply_ticket_acl(
            session,
            user_id=None,
            customer_user_login=login,
            data={k: k for k in labels},
            return_type="Ticket",
            return_sub_type=name,
            ticket_id=ticket_id,
            action=actor.frontend_action,
            process=process,
            sysconfig=sysconfig,
        )
        out[name] = [
            ProcessDialogOptionOut(value=o.value, label=o.label)
            for o in sorted_options({k: labels[k] for k in kept if k in labels})
        ]
    return out


async def _default_queue_id(session: AsyncSession, default: Any) -> int | None:
    """The dialog's ``Queue`` default (an id, or a queue name) as an id, for the start form."""
    text = str(default).strip() if default is not None else ""
    if not text:
        return None
    if text.isdecimal():
        return int(text)
    row = (
        await session.execute(
            select(Queue.id).where(Queue.name == text, Queue.valid_id == VALID_ID)
        )
    ).scalar_one_or_none()
    return int(row) if row is not None else None


async def _agent_options(
    session: AsyncSession,
    user: AuthenticatedUser,
    names: Sequence[str],
    *,
    ticket_id: int | None,
    dialog_fields: Mapping[str, ActivityDialogFieldConfig],
    dynamic_fields: Mapping[str, DynamicField],
    process: Mapping[str, str],
) -> dict[str, list[ProcessDialogOptionOut]]:
    """Options by *dialog field name*, from the wave-0 ACL-filtered source (with the process
    context, like AgentTicketProcess.pm:4319 passing ``%{$Param{GetParam}}``)."""
    requested: dict[str, str] = {}  # option-source key -> dialog field name
    for name in names:
        if name in _ATTRIBUTE_KEY:
            requested[_ATTRIBUTE_KEY[name]] = name
        elif _field_kind(name, dynamic_fields) in _DF_OPTION_KINDS:
            requested[name] = name
    out: dict[str, list[ProcessDialogOptionOut]] = {}
    if requested:
        queue_id = None
        if ticket_id is None:
            if "Queue" in dialog_fields:
                queue_id = await _default_queue_id(session, dialog_fields["Queue"].default_value)
            if queue_id is None:
                # AgentTicketProcess.pm:6420 -- no Queue yet: scope the agent pickers to
                # Process::DefaultQueue.
                queue_id = await _default_queue_id(
                    session, await SysConfig(session).get("Process::DefaultQueue", "Raw")
                )
        collected = await collect_field_options(
            session,
            user,
            FieldOptionsRequest(
                fields=list(requested),
                context=FieldOptionsContext(
                    action="AgentTicketProcess", ticket_id=ticket_id, queue_id=queue_id
                ),
            ),
            process=process,
        )
        for key, options in collected.options.items():
            out[requested[key]] = [
                ProcessDialogOptionOut(value=o.value, label=o.label) for o in options
            ]
    return out


def _customer_field_names(visible: Sequence[str], agent: bool) -> list[str]:
    """One ``customer`` field named ``CustomerUserID`` carries ``CustomerUserID`` and
    ``CustomerID``; the customer interface has no such field (the actor is the customer)."""
    names: list[str] = []
    for name in visible:
        if name in _CUSTOMER_FIELDS:
            if not agent:
                continue
            name = "CustomerUserID"
        if name not in names:
            names.append(name)
    return names


def _default_value(name: str, cfg: ActivityDialogFieldConfig, df: DynamicField | None) -> Any:
    if cfg.default_value is not None and str(cfg.default_value) != "":
        return cfg.default_value
    if df is not None:
        df_default = config_from_yaml(df.config).get("DefaultValue")
        if df_default is not None and str(df_default) != "":
            return df_default
    return None


async def build_dialog_form(
    session: AsyncSession,
    *,
    actor: ProcessActor,
    user: AuthenticatedUser | None,
    graph: ProcessGraph,
    dialog: ActivityDialogNode,
    ticket_id: int | None,
    activity_entity_id: str | None = None,
) -> ProcessDialogFormOut:
    """The form for *dialog*.

    *activity_entity_id* is the ticket's current activity, or the start activity of a start form
    (the ``ActivityEntityID`` of the ACL process context). *user* is the agent for agent routes
    and ``None`` for customers. Availability (state, interface, permission, ACLs) is the
    caller's job (:func:`build_start_form` / :func:`build_ticket_dialog_form`)."""
    agent = actor.interface == "AgentInterface"
    if agent and user is None:
        raise ValueError("agent forms need the AuthenticatedUser")
    config = dialog.config
    visible = visible_fields(config, actor.interface)
    names = _customer_field_names(visible, agent)
    dynamic_fields = await _load_dynamic_fields(session, names)
    dialog_fields = {name: field_config(config, name) for name in names}
    process_context = {
        "ProcessEntityID": graph.entity_id,
        "ActivityEntityID": activity_entity_id or "",
        "ActivityDialogEntityID": dialog.entity_id,
    }

    if agent:
        assert user is not None
        options = await _agent_options(
            session,
            user,
            names,
            ticket_id=ticket_id,
            dialog_fields=dialog_fields,
            dynamic_fields=dynamic_fields,
            process=process_context,
        )
    else:
        default_queue_id = None
        if ticket_id is None and "Queue" in dialog_fields:
            default_queue_id = await _default_queue_id(
                session, dialog_fields["Queue"].default_value
            )
        wanted = [_ATTRIBUTE_KEY[n] for n in names if n in _ATTRIBUTE_KEY]
        wanted += [n for n in names if _field_kind(n, dynamic_fields) in _DF_OPTION_KINDS]
        collected = await collect_customer_field_options(
            session,
            actor,
            wanted,
            ticket_id,
            process=process_context,
            dynamic_fields=dynamic_fields,
            queue_id=default_queue_id,
        )
        options = {
            name: collected.get(_ATTRIBUTE_KEY.get(name, name), [])
            for name in names
            if name in _ATTRIBUTE_KEY or _field_kind(name, dynamic_fields) in _DF_OPTION_KINDS
        }

    fields: list[ProcessDialogFieldOut] = []
    for name in names:
        cfg = dialog_fields[name]
        df = dynamic_fields.get(name)
        required = cfg.required
        if name == "CustomerUserID":
            required = any(
                field_config(config, c).required for c in _CUSTOMER_FIELDS if c in visible
            )
        kind = _field_kind(name, dynamic_fields)
        if name == "Lock":
            field_options = [
                ProcessDialogOptionOut(value="lock", label="lock"),
                ProcessDialogOptionOut(value="unlock", label="unlock"),
            ]
        else:
            field_options = options.get(name, [])
        fields.append(
            ProcessDialogFieldOut(
                name=name,
                kind=kind,
                label=df.label if df is not None else name,
                required=required,
                description_short=cfg.description_short,
                description_long=cfg.description_long,
                default=_default_value(name, cfg, df),
                options=field_options,
            )
        )
    return ProcessDialogFormOut(
        process_entity_id=graph.entity_id,
        activity_dialog_entity_id=dialog.entity_id,
        name=dialog.name,
        description_short=config.description_short,
        description_long=config.description_long,
        submit_button_text=config.submit_button_text,
        submit_advice_text=config.submit_advice_text,
        fields=fields,
    )


async def build_start_form(
    session: AsyncSession,
    *,
    actor: ProcessActor,
    user: AuthenticatedUser | None,
    process_entity_id: str,
    ticket_id: int | None,
    repository: ProcessRepository,
) -> ProcessDialogFormOut:
    """The start dialog of a process the actor may start (same gate as the start itself:
    :func:`assert_can_start`; *ticket_id* = enroll context)."""
    graph = await assert_can_start(
        session,
        actor,
        process_entity_id=process_entity_id,
        ticket_id=ticket_id,
        repository=repository,
    )
    dialog_id = graph.config.start_activity_dialog
    dialog = await repository.get_activity_dialog(dialog_id) if dialog_id else None
    if dialog is None:  # assert_can_start already demanded it; a race with a redeploy
        raise ProcessNotFound(process_entity_id)
    return await build_dialog_form(
        session,
        actor=actor,
        user=user,
        graph=graph,
        dialog=dialog,
        ticket_id=ticket_id,
        activity_entity_id=graph.config.start_activity,
    )


async def build_ticket_dialog_form(
    session: AsyncSession,
    *,
    actor: ProcessActor,
    user: AuthenticatedUser | None,
    ticket_id: int,
    activity_dialog_entity_id: str,
    repository: ProcessRepository,
) -> ProcessDialogFormOut:
    """A dialog of the ticket's current activity the actor may submit
    (:func:`assert_can_submit`: running, on the activity, interface, permission, ACLs)."""
    candidates = await get_ticket_process_candidates(session, ticket_id, repository=repository)
    if candidates is None:
        raise TicketNotInProcess(f"ticket {ticket_id} is not in a process")
    dialog = await assert_can_submit(
        session,
        actor,
        ticket_id=ticket_id,
        candidates=candidates,
        activity_dialog_entity_id=activity_dialog_entity_id,
    )
    return await build_dialog_form(
        session,
        actor=actor,
        user=user,
        graph=candidates.process,
        dialog=dialog,
        ticket_id=ticket_id,
        activity_entity_id=candidates.activity_entity_id,
    )
