"""ProcessManagement TransitionAction registry: handler + designer parameter specs.

One :class:`TransitionActionModule` per Znuny module
``Kernel::System::ProcessManagement::TransitionAction::<Name>``. The engine runs
``run`` (:func:`tiqora.process.engine.execute_transition_action` via
:func:`resolve_action`); the process designer builds its typed action form from
``params``.

Parameter specs are copied from each module's POD ``Config => {…}`` block
(Znuny 6.5.22 ``Kernel/System/ProcessManagement/TransitionAction/<Name>.pm``),
corrected against the module code where the POD is stale (cited per module).
Not listed on purpose:

- ``UserID`` / ``DynamicField_UserID``: every module accepts them
  (``Base.pm _OverrideUserID`` 245-254); the designer catalogue adds ``UserID``
  implicitly, so it is never part of ``params`` (ruling F18).
- Free dynamic-field keys (``DynamicFieldSet``, ``DynamicFieldRemove``,
  ``DynamicFieldIncrement``, ``TicketCreate``'s ``DynamicField_<Name>``,
  ``TicketArticleCreate``'s ``DynamicField_<Name>``): the designer shows them as
  raw key/value rows.
- Rarely used internal keys (``TN``, ``ParentID``, ``UniqueID``, ``RecurringRaw``,
  ``RecurrenceID``, ``Notification*``, ``TicketAppointmentRuleID``, the
  ``No``/``User`` framework aliases of ``TicketCustomerSet``): raw rows as well.
- The ``Attachments``/``AttachmentIDs``/``AttachmentsReuse`` keys of
  ``ArticleSend``/``TicketArticleCreate`` (added together with their handler
  support by plan Task 5).

This module imports nothing from :mod:`tiqora.process.engine` at import time:
:func:`core_transition_actions` imports the engine handlers lazily inside the
function (ruling F22), so the engine can import :func:`resolve_action` at
module level.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.extensions.registry import ExtensionPoint
from tiqora.znuny.sysconfig import SysConfig

ZNUNY_MODULE_PREFIX = "Kernel::System::ProcessManagement::TransitionAction::"


class ActionUnsupported(Exception):  # noqa: N818 - the engine's signal, not an error class
    """Raised by a handler that cannot act in the current setup (e.g. ``ConfigItemUpdate``
    outside ITSM mode ``tiqora``); :func:`tiqora.process.engine.execute_transition_action`
    reports the module as unsupported instead of failing the transition."""


ActionHandler = Callable[[AsyncSession, dict[str, Any], int, int, SysConfig], Awaitable[None]]
"""``(session, config, ticket_id, user_id, sysconfig)``; ``config`` has smart tags replaced."""

ParamKind = Literal[
    "text",
    "textarea",
    "number",
    "bool",
    "datetime",
    "queue",
    "state",
    "priority",
    "type",
    "service",
    "sla",
    "user",
    "customer_user",
    "dynamic_field",
    "calendar",
    "lock",
    "link_type",
    "std_attachment",
    "ticket_ref",
]


@dataclass(frozen=True, slots=True)
class ActionParam:
    """One Znuny ``Config`` key of a transition action.

    ``one_of`` names alternative keys for the same value, the name form first
    (``("State", "StateID")``); ``name`` is always ``one_of[0]`` and ``kind`` is
    the kind of the name form. Znuny checks such a group as "at least one is
    set" (``if ( !$Param{Config}->{StateID} && !$Param{Config}->{State} )``,
    ``TicketStateSet.pm:115``) and prefers the ID when both are given, so the
    designer offers one of the two ("Name oder ID"). ``required`` on a group
    means one key of the group must be set.
    """

    name: str
    kind: ParamKind
    required: bool = False
    one_of: tuple[str, ...] = ()
    list_value: bool = False


@dataclass(frozen=True, slots=True)
class TransitionActionModule:
    name: str
    """Short name, e.g. ``"TicketStateSet"``."""
    run: ActionHandler | None
    """``None``: known to Znuny but not run by Tiqora (designer: "nur in Znuny wirksam")."""
    params: tuple[ActionParam, ...] = ()
    tiqora_only: bool = False
    """``Kernel::System::ProcessManagement::TransitionAction::Tiqora*`` (none yet)."""

    @property
    def znuny_module(self) -> str:
        return ZNUNY_MODULE_PREFIX + self.name


TRANSITION_ACTIONS: ExtensionPoint[TransitionActionModule] = ExtensionPoint(
    key="core.process.transition_actions",
    item_key=lambda m: m.name,
    description="ProcessManagement transition action modules (handler + designer parameter specs)",
)


def _pair(name: str, kind: ParamKind, *, required: bool = False) -> ActionParam:
    """``X`` / ``XID`` alternative pair (``Queue``/``QueueID``, …)."""
    return ActionParam(name, kind, required=required, one_of=(name, name + "ID"))


# --- parameter specs (Znuny 6.5.22 POD + code) ------------------------------------------

# TicketStateSet.pm POD + :115 (State or StateID required).
_TICKET_STATE_SET = (
    _pair("State", "state", required=True),
    ActionParam("PendingTime", "datetime"),
    ActionParam("PendingTimeDiff", "number"),
)
# TicketQueueSet.pm:104, TicketOwnerSet.pm:104, TicketResponsibleSet.pm:105,
# TicketPrioritySet.pm:93, TicketLockSet.pm:104, TicketTypeSet.pm:106,
# TicketServiceSet.pm:106, TicketSLASet.pm:105 — each "X or XID" required.
_TICKET_QUEUE_SET = (_pair("Queue", "queue", required=True),)
_TICKET_OWNER_SET = (_pair("Owner", "user", required=True),)
_TICKET_RESPONSIBLE_SET = (_pair("Responsible", "user", required=True),)
_TICKET_PRIORITY_SET = (_pair("Priority", "priority", required=True),)
_TICKET_LOCK_SET = (_pair("Lock", "lock", required=True),)
_TICKET_TYPE_SET = (_pair("Type", "type", required=True),)
_TICKET_SERVICE_SET = (_pair("Service", "service", required=True),)
_TICKET_SLA_SET = (_pair("SLA", "sla", required=True),)
# TicketTitleSet.pm:104 (Title must be defined).
_TICKET_TITLE_SET = (ActionParam("Title", "text", required=True),)
# TicketCustomerSet.pm:110-121: at least one of CustomerID/No/CustomerUserID/User; both
# may be set together, so this is not an alternative pair (validation of "one of the
# two" is left to the designer validation).
_TICKET_CUSTOMER_SET = (
    ActionParam("CustomerID", "text"),
    ActionParam("CustomerUserID", "customer_user"),
)
# TicketWatchSet.pm:126-200: Action defaults to Subscribe; one of the three user lists
# (or Action UnsubscribeAll).
_TICKET_WATCH_SET = (
    ActionParam("Action", "text"),
    ActionParam("UserLogin", "user", list_value=True),
    ActionParam("PostMasterSearch", "text", list_value=True),
    ActionParam("UserIDs", "user", list_value=True),
)
# DynamicFieldSet.pm:110-150 / DynamicFieldRemove.pm:110-140 iterate the free
# DynamicField-name keys (DynamicFieldRemove's POD "FieldName" is that placeholder).
_DYNAMIC_FIELD_SET: tuple[ActionParam, ...] = ()
_DYNAMIC_FIELD_REMOVE: tuple[ActionParam, ...] = ()
# DynamicFieldIncrement.pm:116 (Value, default 1) + free DF-name keys.
_DYNAMIC_FIELD_INCREMENT = (ActionParam("Value", "number"),)
# DynamicFieldPendingTimeSet.pm:104-106 (State is read by the code, not in the POD).
_DYNAMIC_FIELD_PENDING_TIME_SET = (
    ActionParam("DynamicField", "dynamic_field", required=True),
    ActionParam("Offset", "text"),
    ActionParam("State", "state"),
)
# LinkAdd.pm POD: every key required.
_LINK_ADD = (
    ActionParam("SourceObject", "text", required=True),
    ActionParam("SourceKey", "ticket_ref", required=True),
    ActionParam("TargetObject", "text", required=True),
    ActionParam("TargetKey", "ticket_ref", required=True),
    ActionParam("Type", "link_type", required=True),
    ActionParam("State", "text", required=True),
)
# ArticleSend.pm:163-200 / TicketArticleCreate.pm:297-330: reuse of the dialog's `Attachments`
# selection and standard attachments (comma separated names / ids).
_ARTICLE_ATTACHMENT_PARAMS = (
    ActionParam("AttachmentsReuse", "bool"),
    ActionParam("Attachments", "std_attachment", list_value=True),
    ActionParam("AttachmentIDs", "text"),
)
# ArticleSend.pm POD (To/Cc/Bcc: at least one of the three).
_ARTICLE_SEND = (
    ActionParam("SenderType", "text", required=True, one_of=("SenderType", "SenderTypeID")),
    ActionParam("IsVisibleForCustomer", "bool", required=True),
    # Optional although the POD lists it: ArticleSend.pm:162 always replaces From via FromGet
    # (378-432: queue system address fallback); ported as engine._article_send_from.
    ActionParam("From", "text"),
    ActionParam("To", "text"),
    ActionParam("Cc", "text"),
    ActionParam("Bcc", "text"),
    ActionParam("ReplyTo", "text"),
    ActionParam("Subject", "text", required=True),
    ActionParam("Body", "textarea", required=True),
    ActionParam("InReplyTo", "text"),
    ActionParam("References", "text"),
    ActionParam("Charset", "text"),
    ActionParam("MimeType", "text"),
    ActionParam("Loop", "bool"),
    ActionParam("HistoryType", "text"),
    ActionParam("HistoryComment", "text"),
    ActionParam("NoAgentNotify", "bool"),
    _pair("Queue", "queue"),
    *_ARTICLE_ATTACHMENT_PARAMS,
    ActionParam("Template", "text", one_of=("Template", "TemplateID")),
    ActionParam("Salutation", "text", one_of=("Salutation", "SalutationID")),
    ActionParam("Signature", "text", one_of=("Signature", "SignatureID")),
    ActionParam("UseTicketHook", "bool"),
)
# TicketArticleCreate.pm POD + :222-230 (SenderType, IsVisibleForCustomer required;
# CommunicationChannel defaults to Internal) + the channel's ArticleCreate payload.
_ARTICLE_PAYLOAD = (
    ActionParam("From", "text"),
    ActionParam("To", "text"),
    ActionParam("Cc", "text"),
    ActionParam("Bcc", "text"),
    ActionParam("Subject", "text"),
    ActionParam("Body", "textarea"),
    ActionParam("ContentType", "text"),
    ActionParam("MimeType", "text"),
    ActionParam("Charset", "text"),
    ActionParam("HistoryType", "text"),
    ActionParam("HistoryComment", "text"),
    ActionParam("NoAgentNotify", "bool"),
    ActionParam("TimeUnit", "number"),
)
_TICKET_ARTICLE_CREATE = (
    ActionParam("SenderType", "text", required=True),
    ActionParam("IsVisibleForCustomer", "bool", required=True),
    ActionParam("CommunicationChannel", "text"),
    *_ARTICLE_PAYLOAD,
    *_ARTICLE_ATTACHMENT_PARAMS,
)
# TicketCreate.pm POD + :197-217: Queue/State/Lock/Priority fall back to
# Process::Default*, so nothing is required; the article is created only when
# SenderType and IsVisibleForCustomer are set (:302).
_TICKET_CREATE = (
    ActionParam("Title", "text"),
    _pair("Queue", "queue"),
    _pair("Lock", "lock"),
    _pair("Priority", "priority"),
    _pair("State", "state"),
    ActionParam("CustomerID", "text"),
    ActionParam("CustomerUser", "customer_user"),
    _pair("Owner", "user"),
    _pair("Type", "type"),
    _pair("Service", "service"),
    _pair("SLA", "sla"),
    _pair("Responsible", "user"),
    ActionParam("ArchiveFlag", "text"),
    ActionParam("PendingTime", "datetime"),
    ActionParam("PendingTimeDiff", "number"),
    ActionParam("SenderType", "text"),
    ActionParam("IsVisibleForCustomer", "bool"),
    ActionParam("CommunicationChannel", "text"),
    *_ARTICLE_PAYLOAD,
    ActionParam("Attachments", "bool"),
    ActionParam("LinkAs", "link_type"),
)
# AppointmentCreate.pm / AppointmentUpdate.pm POD; TeamID, ResourceID,
# RecurrenceFrequency, RecurrenceExclude are comma-split (:161 / :160).
_CALENDAR = ActionParam(
    "CalendarName", "calendar", required=True, one_of=("CalendarName", "CalendarID")
)
_APPOINTMENT_FIELDS = (
    ActionParam("Title", "text", required=True),
    ActionParam("StartTime", "datetime", required=True),
    ActionParam("EndTime", "datetime", required=True),
    ActionParam("Description", "textarea"),
    ActionParam("Location", "text"),
    ActionParam("AllDay", "bool"),
    ActionParam("TeamID", "text", list_value=True),
    ActionParam("ResourceID", "user", list_value=True),
    ActionParam("Recurring", "bool"),
    ActionParam("RecurrenceType", "text"),
    ActionParam("RecurrenceFrequency", "text", list_value=True),
    ActionParam("RecurrenceCount", "number"),
    ActionParam("RecurrenceInterval", "number"),
    ActionParam("RecurrenceUntil", "datetime"),
    ActionParam("RecurrenceExclude", "datetime", list_value=True),
)
_APPOINTMENT_CREATE = (
    _CALENDAR,
    *_APPOINTMENT_FIELDS,
    ActionParam("DynamicField_AppointmentID", "dynamic_field"),
)
_APPOINTMENT_UPDATE = (
    _CALENDAR,
    ActionParam("AppointmentID", "text", required=True),
    *_APPOINTMENT_FIELDS,
)
# AppointmentRemove.pm:96 (AppointmentID required).
_APPOINTMENT_REMOVE = (ActionParam("AppointmentID", "text", required=True),)
# ExecuteInvoker.pm POD + :109-122 (Webservice is a name, not an id). ``Asynchronous`` is
# accepted and ignored: the GI handler always queues the call (cross-plan ruling H1); the
# designer says so next to the field. ``UserID`` comes from the catalogue (ruling F18).
_EXECUTE_INVOKER = (
    ActionParam("Webservice", "text", required=True),
    ActionParam("Invoker", "text", required=True),
    ActionParam("Asynchronous", "bool"),
)
# ConfigItemUpdate.pm POD: ConfigItemID or ConfigItemNumber (comma lists) + free
# attribute keys.
_CONFIG_ITEM_UPDATE = (
    ActionParam(
        "ConfigItemID",
        "text",
        required=True,
        one_of=("ConfigItemID", "ConfigItemNumber"),
        list_value=True,
    ),
    ActionParam("Name", "text"),
    ActionParam("DeplStateName", "text"),
    ActionParam("InciStateName", "text"),
    ActionParam("Separator", "text"),
)


def core_transition_actions() -> list[TransitionActionModule]:
    """Every Znuny transition action module, with Tiqora's handler where one exists."""
    from tiqora.gi.process_action import execute_invoker_action
    from tiqora.itsm import config_item_update
    from tiqora.process import engine
    from tiqora.process.actions import appointments

    def m(
        name: str, run: ActionHandler | None, params: tuple[ActionParam, ...]
    ) -> TransitionActionModule:
        return TransitionActionModule(name=name, run=run, params=params)

    return [
        m("TicketStateSet", engine._action_ticket_state_set, _TICKET_STATE_SET),
        m("TicketQueueSet", engine._action_ticket_queue_set, _TICKET_QUEUE_SET),
        m("TicketOwnerSet", engine._action_ticket_owner_set, _TICKET_OWNER_SET),
        m("TicketPrioritySet", engine._action_ticket_priority_set, _TICKET_PRIORITY_SET),
        m("TicketTitleSet", engine._action_ticket_title_set, _TICKET_TITLE_SET),
        m("TicketCustomerSet", engine._action_ticket_customer_set, _TICKET_CUSTOMER_SET),
        m(
            "TicketResponsibleSet",
            engine._action_ticket_responsible_set,
            _TICKET_RESPONSIBLE_SET,
        ),
        m("TicketLockSet", engine._action_ticket_lock_set, _TICKET_LOCK_SET),
        m("DynamicFieldSet", engine._action_dynamic_field_set, _DYNAMIC_FIELD_SET),
        m("DynamicFieldRemove", engine._action_dynamic_field_remove, _DYNAMIC_FIELD_REMOVE),
        m(
            "DynamicFieldIncrement",
            engine._action_dynamic_field_increment,
            _DYNAMIC_FIELD_INCREMENT,
        ),
        m(
            "DynamicFieldPendingTimeSet",
            engine._action_dynamic_field_pending_time_set,
            _DYNAMIC_FIELD_PENDING_TIME_SET,
        ),
        m(
            "TicketArticleCreate",
            engine._action_ticket_article_create,
            _TICKET_ARTICLE_CREATE,
        ),
        m("ArticleSend", engine._action_article_send, _ARTICLE_SEND),
        m("TicketTypeSet", engine._action_ticket_type_set, _TICKET_TYPE_SET),
        m("TicketServiceSet", engine._action_ticket_service_set, _TICKET_SERVICE_SET),
        m("TicketSLASet", engine._action_ticket_sla_set, _TICKET_SLA_SET),
        m("TicketWatchSet", engine._action_ticket_watch_set, _TICKET_WATCH_SET),
        m("LinkAdd", engine._action_link_add, _LINK_ADD),
        m("TicketCreate", engine._action_ticket_create, _TICKET_CREATE),
        m("AppointmentCreate", appointments.action_appointment_create, _APPOINTMENT_CREATE),
        m("AppointmentUpdate", appointments.action_appointment_update, _APPOINTMENT_UPDATE),
        m("AppointmentRemove", appointments.action_appointment_remove, _APPOINTMENT_REMOVE),
        # ExecuteInvoker: the handler is owned by the GI requester (``tiqora/gi/process_action``,
        # imported lazily above: it must not import tiqora.process, ruling H1). It queues the
        # invoker call as a job in the caller's transaction; no own implementation here.
        m("ExecuteInvoker", execute_invoker_action, _EXECUTE_INVOKER),
        # ConfigItemUpdate acts on the Tiqora CMDB (mode ``tiqora`` only; elsewhere it raises
        # ``ActionUnsupported``). Lazy import: the itsm package imports this module.
        m("ConfigItemUpdate", config_item_update.action_config_item_update, _CONFIG_ITEM_UPDATE),
    ]


def module_short_name(module: str) -> str:
    """Last segment of a Perl-style ``Kernel::System::...::Foo`` module path."""
    if "::" in module:
        return module.rsplit("::", 1)[-1]
    if "." in module:
        return module.rsplit(".", 1)[-1]
    return module


def resolve_action(module: str) -> TransitionActionModule | None:
    """Registry entry for a full Perl module path or a short name; ``None`` if unknown."""
    from tiqora.extensions.core import get_registry

    return get_registry().get(TRANSITION_ACTIONS, module_short_name(module))
