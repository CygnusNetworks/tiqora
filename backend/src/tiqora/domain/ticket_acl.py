"""Znuny-compatible Ticket ACL evaluation.

Mirrors ``Kernel/System/Ticket/TicketACL.pm`` for the data-filtering path:

* Load valid ``acl`` rows ordered by name (Znuny ``sort keys %Acls``).
* Parse ``config_match`` / ``config_change`` YAML (1:1 DB text storage).
* Match ``Properties`` and ``PropertiesDatabase`` (both must match when both
  present; a missing side inherits the other side's result).
* Apply ``Possible`` (replace whitelist), ``PossibleAdd`` (union), and
  ``PossibleNot`` (subtract) for ``ReturnType`` Action/Ticket (and Process /
  ActivityDialog specials treated like Action).
* ``UserID == 1`` is never restricted (Znuny root).
* ``stop_after_match`` stops further ACL evaluation after a productive match.

This filters selectable values / actions only. Group/role queue permissions
(:class:`~tiqora.permissions.engine.PermissionEngine`) remain authoritative
for access control.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.legacy.config import Acl
from tiqora.db.legacy.dynamic_field import DynamicField, DynamicFieldValue
from tiqora.db.legacy.queue import Queue, Service, Sla
from tiqora.db.legacy.ticket import Ticket, TicketPriority, TicketState, TicketType
from tiqora.db.legacy.user import (
    GroupRole,
    GroupUser,
    PermissionGroups,
    Roles,
    RoleUser,
    Users,
)
from tiqora.permissions.engine import PERMISSION_KEYS

logger = logging.getLogger(__name__)

_VALID = 1
_ROOT_USER_ID = 1

# Return types that store Possible* under ``{ReturnType}: [items]`` rather
# than ``Ticket: {ReturnSubType: [items]}``.
_SPECIAL_RETURN_TYPES = frozenset({"Action", "Process", "ActivityDialog"})

# ---------------------------------------------------------------------------
# Match primitives (pure — unit-tested without DB)
# ---------------------------------------------------------------------------


def compare_match_with_data(
    match: str,
    data: str,
    *,
    single_item: bool = True,
) -> dict[str, Any]:
    """Znuny ``_CompareMatchWithData`` semantics.

    Supports:

    * plain string equality
    * ``[Not]value``, ``[NotRegExp]pat``, ``[Notregexp]pat``
    * ``[RegExp]pat``, ``[regexp]pat``
    * ``/pattern/`` and ``/pattern/i`` (convenience; maps to regexp forms)

    Returns ``{"match": bool, "skip": bool}``.
    """
    match_s = str(match) if match is not None else ""
    data_s = str(data) if data is not None else ""

    # Negated matches.
    if match_s.startswith("[Not"):
        if match_s.startswith("[Not]"):
            not_value = match_s[len("[Not]") :]
            if not_value == data_s:
                return {"match": False, "skip": False}
        elif match_s.startswith("[NotRegExp]"):
            pattern = match_s[len("[NotRegExp]") :]
            if _re_search(pattern, data_s, ignore_case=False):
                return {"match": False, "skip": False}
        elif match_s.startswith("[Notregexp]"):
            pattern = match_s[len("[Notregexp]") :]
            if _re_search(pattern, data_s, ignore_case=True):
                return {"match": False, "skip": False}

        if single_item:
            return {"match": True, "skip": False}
        return {"match": True, "skip": True}

    # Positive equality.
    if match_s == data_s:
        return {"match": True, "skip": False}

    # Znuny [RegExp] / [regexp]
    if match_s.startswith("[RegExp]"):
        pattern = match_s[len("[RegExp]") :]
        if _re_search(pattern, data_s, ignore_case=False):
            return {"match": True, "skip": False}
    elif match_s.startswith("[regexp]"):
        pattern = match_s[len("[regexp]") :]
        if _re_search(pattern, data_s, ignore_case=True):
            return {"match": True, "skip": False}

    # Convenience /pattern/ and /pattern/i (not native Znuny TicketACL, but
    # commonly written in hand-edited YAML and requested by our API surface).
    slash = _slash_regex(match_s)
    if slash is not None:
        pattern, ignore_case = slash
        if _re_search(pattern, data_s, ignore_case=ignore_case):
            return {"match": True, "skip": False}

    if single_item:
        return {"match": False, "skip": False}
    return {"match": False, "skip": True}


def _re_search(pattern: str, data: str, *, ignore_case: bool) -> bool:
    flags = re.IGNORECASE if ignore_case else 0
    try:
        return re.search(pattern, data, flags) is not None
    except re.error:
        logger.debug("Invalid ACL regex %r", pattern)
        return False


def _slash_regex(match: str) -> tuple[str, bool] | None:
    """Parse ``/pattern/`` or ``/pattern/i`` into (pattern, ignore_case)."""
    if len(match) < 2 or match[0] != "/":
        return None
    # Trailing /i or /
    if match.endswith("/i") and len(match) >= 3:
        return match[1:-2], True
    if match.endswith("/") and len(match) >= 2:
        return match[1:-1], False
    return None


def _as_str_list(raw: Any) -> list[str]:
    """Normalise YAML match/change list items to strings."""
    if raw is None:
        return []
    if isinstance(raw, list | tuple):
        return ["" if v is None else str(v) for v in raw]
    # Single scalar written without list brackets.
    return [str(raw)]


def _check_value_as_items(value: Any) -> list[str] | str | None:
    """Return check data as either a list (ARRAY semantics) or a scalar."""
    if value is None:
        return None
    if isinstance(value, list | tuple):
        return ["" if v is None else str(v) for v in value]
    if isinstance(value, dict):
        # Unexpected shape — ignore.
        return None
    return str(value)


def match_property_items(match_items: list[str], check_value: Any) -> bool:
    """True if any *match_items* entry matches *check_value* (scalar or list)."""
    if not match_items:
        return False
    items = _check_value_as_items(check_value)
    if items is None:
        return False

    for item in match_items:
        if isinstance(items, list):
            match_item = item.startswith("[Not")
            for array_data_item in items:
                result = compare_match_with_data(item, array_data_item, single_item=False)
                if not result["skip"]:
                    match_item = bool(result["match"])
                    break
            if match_item:
                return True
        else:
            result = compare_match_with_data(item, items, single_item=True)
            if result["match"]:
                return True
    return False


def properties_block_matches(
    block: dict[str, Any] | None,
    checks: dict[str, Any],
) -> tuple[bool, bool]:
    """Evaluate one Properties / PropertiesDatabase block.

    Returns ``(match, match_try)``. Empty / missing block → ``(True, False)``
    so the peer side can stand in (ForceMatch is handled by the caller when
    *both* blocks are empty).
    """
    if not isinstance(block, dict) or not block:
        return True, False

    match = True
    match_try = False
    for key, fields in block.items():
        if not isinstance(fields, dict):
            match = False
            match_try = True
            continue
        used = checks.get(key) if isinstance(checks.get(key), dict) else {}
        for field_name, raw_items in fields.items():
            match_try = True
            items = _as_str_list(raw_items)
            check_value = used.get(field_name) if used else None
            if not match_property_items(items, check_value):
                match = False
    return match, match_try


def acl_properties_match(
    config_match: dict[str, Any] | None,
    checks: dict[str, Any],
    checks_database: dict[str, Any],
) -> tuple[bool, bool]:
    """Combine Properties + PropertiesDatabase per Znuny TicketACL rules.

    Returns ``(match, match_try)``.
    """
    props = (config_match or {}).get("Properties")
    props_db = (config_match or {}).get("PropertiesDatabase")
    props_ok = isinstance(props, dict) and bool(props)
    props_db_ok = isinstance(props_db, dict) and bool(props_db)

    # Force match when neither side is present.
    if not props_ok and not props_db_ok:
        return True, True

    p_match, p_try = properties_block_matches(props if props_ok else None, checks)
    d_match, d_try = properties_block_matches(props_db if props_db_ok else None, checks_database)

    # Missing side inherits the present side's result.
    if not props_ok:
        p_match, p_try = d_match, d_try
    if not props_db_ok:
        d_match, d_try = p_match, p_try

    return (p_match and d_match), (p_try and d_try)


def apply_possible_filters(
    data: dict[Any, str],
    current: dict[Any, str],
    config_change: dict[str, Any] | None,
    *,
    return_type: str,
    return_sub_type: str,
) -> tuple[dict[Any, str], bool]:
    """Apply Possible / PossibleAdd / PossibleNot for one matched ACL.

    Returns ``(new_data, used)`` where *used* is True if any Possible* section
    applied for this return type/sub-type.
    """
    if not isinstance(config_change, dict):
        return current, False

    used = False
    new_tmp = dict(current)
    special = return_type in _SPECIAL_RETURN_TYPES

    def _rules_for(section: str) -> list[str] | None:
        root = config_change.get(section)
        if not isinstance(root, dict):
            return None
        if special:
            raw = root.get(return_type)
        else:
            ticket = root.get("Ticket")
            if not isinstance(ticket, dict):
                return None
            raw = ticket.get(return_sub_type)
        if raw is None:
            return None
        return _as_str_list(raw)

    possible = _rules_for("Possible")
    if possible is not None:
        used = True
        # Possible resets the whitelist against the *original* data set.
        new_tmp = {}
        for key, value in data.items():
            for rule in possible:
                if compare_match_with_data(rule, value, single_item=True)["match"]:
                    new_tmp[key] = value
                    break

    possible_add = _rules_for("PossibleAdd")
    if possible_add is not None:
        used = True
        for key, value in data.items():
            for rule in possible_add:
                if compare_match_with_data(rule, value, single_item=True)["match"]:
                    new_tmp[key] = value
                    break

    possible_not = _rules_for("PossibleNot")
    if possible_not is not None:
        used = True
        for key, value in list(new_tmp.items()):
            remove = False
            for rule in possible_not:
                if compare_match_with_data(rule, value, single_item=True)["match"]:
                    remove = True
                    break
            if remove:
                del new_tmp[key]

    return new_tmp, used


def parse_acl_yaml(raw: str | bytes | None) -> dict[str, Any]:
    """Parse ACL YAML text; empty / invalid → ``{}``."""
    if raw is None:
        return {}
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
    text = text.strip()
    if not text:
        return {}
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError:
        logger.warning("Failed to parse ACL YAML")
        return {}
    return loaded if isinstance(loaded, dict) else {}


# ---------------------------------------------------------------------------
# Check-context builders
# ---------------------------------------------------------------------------


async def load_user_checks(session: AsyncSession, user_id: int) -> dict[str, Any]:
    """Build Znuny-style ``User`` check hash (UserID, Group_*, Role, …)."""
    user = await session.get(Users, user_id)
    if user is None:
        return {"UserID": str(user_id)}

    checks: dict[str, Any] = {
        "UserID": str(user.id),
        "UserLogin": user.login,
        "UserFirstname": user.first_name,
        "UserLastname": user.last_name,
        "UserFullname": f"{user.first_name} {user.last_name}".strip(),
    }

    # Direct + role-derived group memberships by permission key → group names.
    valid_groups = {
        row.id: row.name
        for row in (
            await session.execute(
                select(PermissionGroups).where(PermissionGroups.valid_id == _VALID)
            )
        ).scalars()
    }

    perms_by_group: dict[int, set[str]] = {}
    gu = await session.execute(
        select(GroupUser.group_id, GroupUser.permission_key).where(GroupUser.user_id == user_id)
    )
    for group_id, key in gu.all():
        if group_id in valid_groups and key in PERMISSION_KEYS:
            perms_by_group.setdefault(group_id, set()).add(key)

    role_ids = [
        r[0]
        for r in (
            await session.execute(
                select(RoleUser.role_id)
                .join(Roles, Roles.id == RoleUser.role_id)
                .where(RoleUser.user_id == user_id, Roles.valid_id == _VALID)
            )
        ).all()
    ]
    if role_ids:
        gr = await session.execute(
            select(GroupRole.group_id, GroupRole.permission_key).where(
                GroupRole.role_id.in_(role_ids),
                GroupRole.permission_value == 1,
            )
        )
        for group_id, key in gr.all():
            if group_id in valid_groups and key in PERMISSION_KEYS:
                perms_by_group.setdefault(group_id, set()).add(key)

    for perm_key in sorted(PERMISSION_KEYS):
        names = sorted(
            valid_groups[gid]
            for gid, keys in perms_by_group.items()
            if perm_key in keys or "rw" in keys
        )
        checks[f"Group_{perm_key}"] = names

    role_names = [
        r[0]
        for r in (
            await session.execute(
                select(Roles.name)
                .join(RoleUser, RoleUser.role_id == Roles.id)
                .where(RoleUser.user_id == user_id, Roles.valid_id == _VALID)
                .order_by(Roles.name)
            )
        ).all()
    ]
    checks["Role"] = role_names
    return checks


async def load_ticket_attribute_checks(
    session: AsyncSession,
    ticket_id: int,
) -> dict[str, Any]:
    """Build ``Ticket`` (and related) checks from DB for PropertiesDatabase.

    Includes Queue/State/Priority/Type/Service/SLA names + ids, owner/responsible
    logins, customer ids, dynamic fields, and Process entity ids when present.
    """
    ticket = await session.get(Ticket, ticket_id)
    if ticket is None:
        return {}

    ticket_checks: dict[str, Any] = {
        "TicketID": str(ticket.id),
        "QueueID": str(ticket.queue_id),
        "StateID": str(ticket.ticket_state_id),
        "PriorityID": str(ticket.ticket_priority_id),
        "OwnerID": str(ticket.user_id),
        "ResponsibleID": str(ticket.responsible_user_id),
        "LockID": str(ticket.ticket_lock_id),
        "CustomerID": ticket.customer_id or "",
        "CustomerUserID": ticket.customer_user_id or "",
        "Title": ticket.title or "",
    }
    if ticket.type_id is not None:
        ticket_checks["TypeID"] = str(ticket.type_id)
    if ticket.service_id is not None:
        ticket_checks["ServiceID"] = str(ticket.service_id)
    if ticket.sla_id is not None:
        ticket_checks["SLAID"] = str(ticket.sla_id)

    # Resolve names for Queue / State / Priority / Type / Service / SLA.
    async def _name(model: Any, pk: int | None) -> str | None:
        if pk is None:
            return None
        row = await session.get(model, pk)
        return row.name if row is not None else None

    queue_name = await _name(Queue, ticket.queue_id)
    if queue_name:
        ticket_checks["Queue"] = queue_name
    state_name = await _name(TicketState, ticket.ticket_state_id)
    if state_name:
        ticket_checks["State"] = state_name
    prio_name = await _name(TicketPriority, ticket.ticket_priority_id)
    if prio_name:
        ticket_checks["Priority"] = prio_name
    type_name = await _name(TicketType, ticket.type_id)
    if type_name:
        ticket_checks["Type"] = type_name
    service_name = await _name(Service, ticket.service_id)
    if service_name:
        ticket_checks["Service"] = service_name
    sla_name = await _name(Sla, ticket.sla_id)
    if sla_name:
        ticket_checks["SLA"] = sla_name

    owner = await session.get(Users, ticket.user_id)
    if owner is not None:
        ticket_checks["Owner"] = owner.login
    resp = await session.get(Users, ticket.responsible_user_id)
    if resp is not None:
        ticket_checks["Responsible"] = resp.login

    # Dynamic fields on the ticket object.
    df_rows = (
        await session.execute(
            select(DynamicField.name, DynamicFieldValue.value_text, DynamicFieldValue.value_int)
            .join(DynamicFieldValue, DynamicFieldValue.field_id == DynamicField.id)
            .where(
                DynamicFieldValue.object_id == ticket_id,
                DynamicField.object_type == "Ticket",
                DynamicField.valid_id == _VALID,
            )
        )
    ).all()
    process: dict[str, Any] = {}
    for name, value_text, value_int in df_rows:
        key = f"DynamicField_{name}"
        if value_text is not None and value_text != "":
            ticket_checks[key] = value_text
        elif value_int is not None:
            ticket_checks[key] = str(value_int)
        # Process management entity ids are commonly stored as DFs.
        if (
            name
            in (
                "ProcessManagementProcessID",
                "ProcessManagementActivityID",
            )
            or name.endswith("ProcessID")
            or name.endswith("ActivityID")
        ):
            val = ticket_checks.get(key)
            if val and "Process" in name:
                process["ProcessEntityID"] = val
            elif val and "Activity" in name:
                process["ActivityEntityID"] = val

    out: dict[str, Any] = {"Ticket": ticket_checks}
    if queue_name:
        out["Queue"] = {"Name": queue_name, "QueueID": str(ticket.queue_id)}
    if process:
        out["Process"] = process
    return out


async def build_checks(
    session: AsyncSession,
    *,
    user_id: int,
    ticket_id: int | None = None,
    action: str | None = None,
    checks: dict[str, Any] | None = None,
    customer_user_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Assemble ``Checks`` (mutable/form) and ``ChecksDatabase`` (DB ticket).

    *checks* may pre-fill/override form-side values (e.g. selected QueueID on
    a create form). Database side is loaded from *ticket_id* when given.
    """
    checks_live: dict[str, Any] = {}
    checks_db: dict[str, Any] = {}

    if action:
        checks_live["Frontend"] = {"Action": action}
        checks_db["Frontend"] = {"Action": action}

    user_block = await load_user_checks(session, user_id)
    checks_live["User"] = user_block
    checks_db["User"] = dict(user_block)

    if ticket_id is not None:
        db_attrs = await load_ticket_attribute_checks(session, ticket_id)
        for key, value in db_attrs.items():
            # Deep-ish copy so live can diverge.
            checks_db[key] = dict(value) if isinstance(value, dict) else value
            checks_live[key] = dict(value) if isinstance(value, dict) else value

    if customer_user_id:
        ticket_live = checks_live.setdefault("Ticket", {})
        if isinstance(ticket_live, dict):
            ticket_live["CustomerUserID"] = customer_user_id

    # Merge caller-supplied form overrides into Checks (not ChecksDatabase).
    if checks:
        for section, payload in checks.items():
            if isinstance(payload, dict):
                target = checks_live.setdefault(section, {})
                if isinstance(target, dict):
                    target.update(payload)
                else:
                    checks_live[section] = dict(payload)
            else:
                checks_live[section] = payload

    return checks_live, checks_db


# ---------------------------------------------------------------------------
# Public evaluation API
# ---------------------------------------------------------------------------


async def load_valid_acls(session: AsyncSession) -> list[Acl]:
    """Valid ACLs ordered by name (Znuny evaluation order)."""
    result = await session.execute(select(Acl).where(Acl.valid_id == _VALID).order_by(Acl.name))
    return list(result.scalars().all())


async def apply_ticket_acl(
    session: AsyncSession,
    *,
    user_id: int,
    data: dict[Any, str],
    return_type: str,
    return_sub_type: str,
    ticket_id: int | None = None,
    action: str | None = None,
    checks: dict[str, Any] | None = None,
    customer_user_id: str | None = None,
) -> dict[Any, str]:
    """Filter *data* (id→name map) through Ticket ACLs.

    If no ACL matches productively, returns a copy of the original *data*.
    UserID 1 is never restricted.
    """
    original = dict(data)
    if not original:
        return original

    if user_id == _ROOT_USER_ID:
        return original

    acls = await load_valid_acls(session)
    if not acls:
        return original

    checks_live, checks_db = await build_checks(
        session,
        user_id=user_id,
        ticket_id=ticket_id,
        action=action,
        checks=checks,
        customer_user_id=customer_user_id,
    )

    new_data = dict(original)
    new_tmp = dict(original)
    any_applied = False

    for acl in acls:
        config_match = parse_acl_yaml(acl.config_match)
        config_change = parse_acl_yaml(acl.config_change)

        matched, match_try = acl_properties_match(config_match, checks_live, checks_db)
        if not (matched and match_try):
            continue

        # Znuny only applies Possible* when there is some check context
        # (live or database). Empty context still allows force-match ACLs
        # that matched above — keep applying when either side has data or
        # the ACL force-matched with empty properties (match_try True).
        filtered, used = apply_possible_filters(
            original,
            new_tmp,
            config_change,
            return_type=return_type,
            return_sub_type=return_sub_type,
        )
        if not used:
            continue

        new_tmp = filtered
        new_data = dict(new_tmp)
        any_applied = True

        if acl.stop_after_match:
            break

    return new_data if any_applied else original


async def filter_id_name_map(
    session: AsyncSession,
    *,
    user_id: int,
    items: dict[int, str] | list[tuple[int, str]],
    return_sub_type: str,
    ticket_id: int | None = None,
    action: str | None = None,
    checks: dict[str, Any] | None = None,
    customer_user_id: str | None = None,
    return_type: str = "Ticket",
) -> dict[int, str]:
    """Convenience wrapper: filter an id→name map for a Ticket return subtype."""
    if isinstance(items, list):
        data: dict[Any, str] = {i: n for i, n in items}
    else:
        data = dict(items)
    filtered = await apply_ticket_acl(
        session,
        user_id=user_id,
        data=data,
        return_type=return_type,
        return_sub_type=return_sub_type,
        ticket_id=ticket_id,
        action=action,
        checks=checks,
        customer_user_id=customer_user_id,
    )
    out: dict[int, str] = {}
    for key, value in filtered.items():
        try:
            out[int(key)] = value
        except (TypeError, ValueError):
            continue
    return out
