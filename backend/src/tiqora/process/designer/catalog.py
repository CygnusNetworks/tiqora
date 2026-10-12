"""Designer catalogue: everything the canvas forms offer (actions, fields, option lists).

Reads the transition action and condition module registries, the ticket attribute and
condition field names, every valid ticket dynamic field and the option lists of queues,
states, priorities, types, services, SLAs and dynamic-field ``PossibleValues``.
"""

from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.legacy.dynamic_field import DynamicField
from tiqora.domain.dynamic_field_config import config_from_yaml
from tiqora.domain.field_options import DF_OPTION_PREFIX, VALID_ID
from tiqora.extensions.core import get_registry
from tiqora.process.actions.registry import (
    TRANSITION_ACTIONS,
    TransitionActionModule,
)
from tiqora.process.condition_modules import CONDITION_MODULES
from tiqora.process.designer.schemas import (
    CatalogActionOut,
    CatalogFieldOut,
    CatalogParamOut,
    DesignerCatalogOut,
)
from tiqora.process.schemas import ProcessDialogOptionOut
from tiqora.util.perl import perl_truthy
from tiqora.znuny.sysconfig import SysConfig

# AdminProcessManagementTransition.pm:532-552 (linking and/or/xor at :518-524).
CONDITION_TYPES = (
    "Module",
    "Regexp",
    "String",
    "Equal",
    "NotEqual",
    "Contains",
    "NotContains",
    "GreaterThan",
    "GreaterThanOrEqual",
    "LessThan",
    "LessThanOrEqual",
)
INTERFACES = ("AgentInterface", "CustomerInterface")
# Znuny ``System::Permission`` default; "" = no permission needed.
DEFAULT_PERMISSIONS = ("ro", "move_into", "create", "note", "owner", "priority", "rw")

# (name, kind) of the ticket attributes a dialog may show. The kind is the dialog form kind
# (``FieldKind``); selects get the option list of the name.
_TICKET_ATTRIBUTES: tuple[tuple[str, str], ...] = (
    ("Title", "text"),
    ("Queue", "select"),
    ("State", "select"),
    ("Priority", "select"),
    ("Type", "select"),
    ("Service", "select"),
    ("SLA", "select"),
    ("Owner", "select"),
    ("Responsible", "select"),
    ("Lock", "select"),
    ("CustomerUserID", "customer"),
    ("CustomerID", "text"),
    ("PendingTime", "pending_time"),
    ("Article", "article"),
    ("Attachments", "ticket_attachments"),
)
# Keys of ``get_condition_data`` that are not dialog fields (Ticket.pm:1240-1420).
_CONDITION_ONLY: tuple[tuple[str, str], ...] = (
    ("TicketID", "text"),
    ("TicketNumber", "text"),
    ("QueueID", "text"),
    ("StateID", "text"),
    ("StateType", "text"),
    ("PriorityID", "text"),
    ("LockID", "text"),
    ("TypeID", "text"),
    ("ServiceID", "text"),
    ("SLAID", "text"),
    ("OwnerID", "text"),
    ("ResponsibleID", "text"),
    ("ArchiveFlag", "text"),
    ("Created", "text"),
    ("Changed", "text"),
)
# Attributes that are condition fields too (they are keys of the condition data).
_CONDITION_ATTRIBUTES = frozenset(
    {
        "Title",
        "Queue",
        "State",
        "Priority",
        "Type",
        "Service",
        "SLA",
        "Owner",
        "Responsible",
        "Lock",
        "CustomerUserID",
        "CustomerID",
    }
)
# name -> SQL (valid rows only; the value is the name, as dialog defaults and conditions use it)
_NAME_LISTS: dict[str, str] = {
    "Queue": "SELECT name FROM queue WHERE valid_id = 1 ORDER BY name",
    "State": "SELECT name FROM ticket_state WHERE valid_id = 1 ORDER BY name",
    "Priority": "SELECT name FROM ticket_priority WHERE valid_id = 1 ORDER BY id",
    "Type": "SELECT name FROM ticket_type WHERE valid_id = 1 ORDER BY name",
    "Service": "SELECT name FROM service WHERE valid_id = 1 ORDER BY name",
    "SLA": "SELECT name FROM sla WHERE valid_id = 1 ORDER BY name",
    "Lock": "SELECT name FROM ticket_lock_type WHERE valid_id = 1 ORDER BY id",
}


def _action_out(module: TransitionActionModule) -> CatalogActionOut:
    params = [
        CatalogParamOut(
            name=p.name,
            kind=p.kind,
            required=p.required,
            one_of=list(p.one_of),
            list_value=p.list_value,
        )
        for p in module.params
    ]
    # ``UserID`` overrides the acting user in every module (Base.pm _OverrideUserID 245-254);
    # the registry leaves it out, the catalogue adds it (ruling F18).
    params.append(
        CatalogParamOut(name="UserID", kind="user", required=False, one_of=[], list_value=False)
    )
    return CatalogActionOut(
        name=module.name,
        module=module.znuny_module,
        supported=module.run is not None,
        tiqora_only=module.tiqora_only,
        params=params,
    )


async def _name_options(session: AsyncSession, attribute: str) -> list[ProcessDialogOptionOut]:
    sql = _NAME_LISTS.get(attribute)
    if sql is None:
        return []
    rows = (await session.execute(text(sql))).scalars().all()
    return [ProcessDialogOptionOut(value=str(n), label=str(n)) for n in rows]


async def _dynamic_fields(session: AsyncSession) -> list[CatalogFieldOut]:
    rows = (
        (
            await session.execute(
                select(DynamicField)
                .where(DynamicField.valid_id == VALID_ID, DynamicField.object_type == "Ticket")
                .order_by(DynamicField.field_order, DynamicField.name)
            )
        )
        .scalars()
        .all()
    )
    fields: list[CatalogFieldOut] = []
    for df in rows:
        possible = config_from_yaml(df.config).get("PossibleValues")
        options = (
            [ProcessDialogOptionOut(value=str(k), label=str(v)) for k, v in possible.items()]
            if isinstance(possible, dict)
            else []
        )
        fields.append(
            CatalogFieldOut(
                name=f"{DF_OPTION_PREFIX}{df.name}",
                label=df.label or df.name,
                kind=df.field_type,
                dialog=True,
                condition=True,
                options=options,
            )
        )
    return fields


async def build_designer_catalog(session: AsyncSession, sysconfig: SysConfig) -> DesignerCatalogOut:
    registry = get_registry()
    actions = sorted(registry.items(TRANSITION_ACTIONS), key=lambda m: m.name)
    modules = sorted(m.match for m in registry.items(CONDITION_MODULES))

    fields: list[CatalogFieldOut] = []
    for name, kind in _TICKET_ATTRIBUTES:
        fields.append(
            CatalogFieldOut(
                name=name,
                label=name,  # the frontend translates ticket attribute names
                kind=kind,
                dialog=True,
                condition=name in _CONDITION_ATTRIBUTES,
                options=await _name_options(session, name),
            )
        )
    fields.extend(
        CatalogFieldOut(name=name, label=name, kind=kind, dialog=False, condition=True)
        for name, kind in _CONDITION_ONLY
    )
    fields.extend(await _dynamic_fields(session))

    configured = await sysconfig.get("System::Permission")
    permissions = (
        [str(p) for p in configured]
        if isinstance(configured, list) and configured
        else list(DEFAULT_PERMISSIONS)
    )
    return DesignerCatalogOut(
        actions=[_action_out(m) for m in actions],
        condition_types=list(CONDITION_TYPES),
        condition_modules=modules,
        fields=fields,
        interfaces=list(INTERFACES),
        permissions=["", *permissions],
        account_time=perl_truthy(await sysconfig.get("Ticket::Frontend::AccountTime")),
    )
