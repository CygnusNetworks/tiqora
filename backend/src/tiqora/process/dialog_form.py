"""Server-built activity dialog forms: visible fields in order, with kind and ACL-filtered options.

The browser never has to interpret ``Display``/``Interface``/field aliases itself: it renders
what :func:`build_dialog_form` returns (plan D9). Znuny references: ``AgentTicketProcess.pm``
``_RenderQueue``/``_RenderState``/... (every option list is a ``TicketAcl`` call with
``Action => AgentTicketProcess``), ``_RenderLock`` (:4013, "no ACL checking yet"),
``CustomerTicketProcess.pm:75-82`` (skipped fields).

Option sources:

* agents -- wave-0 :func:`tiqora.domain.field_options.collect_field_options`, so the dialog
  filters exactly like the zoom pickers (``Owner``/``Responsible`` are all valid agents until
  Task 15 moves them into the ACL-filtered option source),
* customers -- :func:`collect_customer_field_options` (same tables, customer principal,
  ``Action => CustomerTicketProcess``).

Known limitations: agent option ACLs see the ticket's own process (its dynamic fields) but not
the ``ProcessEntityID``/``ActivityDialogEntityID`` of the dialog being opened (the wave-0 option
source has no such parameter); customer Service/SLA lists are not narrowed to the customer's
services.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.legacy.dynamic_field import DynamicField
from tiqora.db.legacy.queue import Queue
from tiqora.db.legacy.user import Users
from tiqora.domain.dynamic_field_config import config_from_yaml
from tiqora.domain.field_options import (
    _FIELD_TO_SUBTYPE,  # noqa: PLC2701 -- deliberate reuse
    _VALID,  # noqa: PLC2701 -- deliberate reuse
    ATTRIBUTE_OPTION_FIELDS,
    DF_OPTION_PREFIX,
    FieldOptionsContext,
    FieldOptionsRequest,
    _base_field_maps,  # noqa: PLC2701 -- deliberate reuse
    _sorted_options,  # noqa: PLC2701 -- deliberate reuse
    collect_field_options,
)
from tiqora.domain.ticket_acl import apply_ticket_acl
from tiqora.process.actor import ProcessActor
from tiqora.process.availability import assert_can_start, assert_can_submit
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
}
_AGENT_PICKERS = frozenset({"Owner", "Responsible"})
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
                    DynamicField.valid_id == _VALID,
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
    if name in _ATTRIBUTE_KEY or name in _AGENT_PICKERS or name == "Lock":
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


async def _customer_queues(session: AsyncSession) -> dict[int, str]:
    rows = (
        await session.execute(
            select(Queue.id, Queue.name)
            .where(Queue.valid_id == _VALID, Queue.name.not_in(sorted(_CUSTOMER_HIDDEN_QUEUES)))
            .order_by(Queue.name)
        )
    ).all()
    return {int(r[0]): r[1] for r in rows}


async def collect_customer_field_options(
    session: AsyncSession,
    actor: ProcessActor,
    fields: Sequence[str],
    ticket_id: int | None,
    *,
    process: Mapping[str, str] | None = None,
    dynamic_fields: Mapping[str, DynamicField] | None = None,
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

    attrs = {f for f in fields if f in ATTRIBUTE_OPTION_FIELDS}
    base = await _base_field_maps(session, None, attrs - {"queue"})
    if "queue" in attrs:
        base["queue"] = await _customer_queues(session)
    for field in (f for f in fields if f in base):
        mapping = base[field]
        kept = await apply_ticket_acl(
            session,
            user_id=None,
            customer_user_login=login,
            data=dict(mapping),
            return_type="Ticket",
            return_sub_type=_FIELD_TO_SUBTYPE[field],
            ticket_id=ticket_id,
            action=actor.frontend_action,
            process=process,
            sysconfig=sysconfig,
        )
        out[field] = [
            ProcessDialogOptionOut(value=str(key), label=label)
            for key, label in mapping.items()
            if key in kept
        ]

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
            for o in _sorted_options({k: labels[k] for k in kept if k in labels})
        ]
    return out


async def _agent_picker_options(session: AsyncSession) -> list[ProcessDialogOptionOut]:
    """``{id: full name}`` of the valid agents, unfiltered until Task 15 (owner/responsible
    join the ACL-filtered option source)."""
    rows = (
        await session.execute(select(Users).where(Users.valid_id == _VALID).order_by(Users.login))
    ).scalars()
    return [
        ProcessDialogOptionOut(value=str(u.id), label=f"{u.first_name} {u.last_name}".strip())
        for u in rows
    ]


async def _default_queue_id(session: AsyncSession, default: Any) -> int | None:
    """The dialog's ``Queue`` default (an id, or a queue name) as an id, for the start form."""
    text = str(default).strip() if default is not None else ""
    if not text:
        return None
    if text.isdecimal():
        return int(text)
    row = (
        await session.execute(select(Queue.id).where(Queue.name == text, Queue.valid_id == _VALID))
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
) -> dict[str, list[ProcessDialogOptionOut]]:
    """Options by *dialog field name*, from the wave-0 ACL-filtered source."""
    requested: dict[str, str] = {}  # option-source key -> dialog field name
    for name in names:
        if name in _ATTRIBUTE_KEY:
            requested[_ATTRIBUTE_KEY[name]] = name
        elif _field_kind(name, dynamic_fields) in _DF_OPTION_KINDS:
            requested[name] = name
    out: dict[str, list[ProcessDialogOptionOut]] = {}
    if requested:
        queue_id = None
        if ticket_id is None and "Queue" in dialog_fields:
            queue_id = await _default_queue_id(session, dialog_fields["Queue"].default_value)
        collected = await collect_field_options(
            session,
            user,
            FieldOptionsRequest(
                fields=list(requested),
                context=FieldOptionsContext(
                    action="AgentTicketProcess", ticket_id=ticket_id, queue_id=queue_id
                ),
            ),
        )
        for key, options in collected.options.items():
            out[requested[key]] = [
                ProcessDialogOptionOut(value=o.value, label=o.label) for o in options
            ]
    if any(n in _AGENT_PICKERS for n in names):
        agents = await _agent_picker_options(session)
        for name in names:
            if name in _AGENT_PICKERS:
                out[name] = list(agents)
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
) -> ProcessDialogFormOut:
    """The form for *dialog*. *user* is the agent for agent routes and ``None`` for customers;
    availability (state, interface, permission, ACLs) is the caller's job
    (:func:`build_start_form` / :func:`build_ticket_dialog_form`)."""
    agent = actor.interface == "AgentInterface"
    if agent and user is None:
        raise ValueError("agent forms need the AuthenticatedUser")
    config = dialog.config
    visible = visible_fields(config, actor.interface, has_ticket=ticket_id is not None)
    names = _customer_field_names(visible, agent)
    dynamic_fields = await _load_dynamic_fields(session, names)
    dialog_fields = {name: field_config(config, name) for name in names}

    if agent:
        assert user is not None
        options = await _agent_options(
            session,
            user,
            names,
            ticket_id=ticket_id,
            dialog_fields=dialog_fields,
            dynamic_fields=dynamic_fields,
        )
    else:
        wanted = [_ATTRIBUTE_KEY[n] for n in names if n in _ATTRIBUTE_KEY]
        wanted += [n for n in names if _field_kind(n, dynamic_fields) in _DF_OPTION_KINDS]
        collected = await collect_customer_field_options(
            session,
            actor,
            wanted,
            ticket_id,
            process={
                "ProcessEntityID": graph.entity_id,
                "ActivityDialogEntityID": dialog.entity_id,
            },
            dynamic_fields=dynamic_fields,
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
        session, actor=actor, user=user, graph=graph, dialog=dialog, ticket_id=ticket_id
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
    )
