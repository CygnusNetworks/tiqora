"""``<OTRS_...>`` placeholder expansion for templates, signatures, auto-responses.

Pragmatic subset of Znuny ``Kernel/System/TemplateGenerator.pm`` ``_Replace``.
Tags are resolved from ticket / queue / agent / customer context when a
``ticket_id`` is provided; callers that only pass a simple ``ticket`` dict
(autoresponse, notifications) keep working.

Unresolved / unknown tags become an empty string (never left as raw
``<OTRS_...>``, never raise). Resolution failures are logged and the original
text is returned so template listing and reply send never break.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.domain.settings_store import get_setting_bool
from tiqora.znuny.sysconfig import SysConfig

logger = structlog.get_logger(__name__)

# tiqora_settings flag: when true, only registered+enabled
# tiqora_placeholder_field tags resolve for CUSTOMER_DATA_*. Default OFF.
KEY_CUSTOMER_ALLOWLIST = "placeholder.customer_allowlist.enabled"

# Znuny matches <OTRS_Name> and <OTRS_Name[n]>; field names are alphanumeric + _.
# <TIQORA_...> is a full alias of <OTRS_...> (configurable vars + stock tags).
_TAG_RE = re.compile(r"<(?:OTRS|TIQORA)_([A-Za-z0-9_]+?)(?:\[(\d+)\])?>", re.IGNORECASE)

# Standard CustomerUser Map (Config Defaults.pm) → DB column.
_CUSTOMER_USER_COL_TO_TAG: dict[str, str] = {
    "title": "UserTitle",
    "first_name": "UserFirstname",
    "last_name": "UserLastname",
    "login": "UserLogin",
    "email": "UserEmail",
    "customer_id": "UserCustomerID",
    "phone": "UserPhone",
    "fax": "UserFax",
    "mobile": "UserMobile",
    "street": "UserStreet",
    "zip": "UserZip",
    "city": "UserCity",
    "country": "UserCountry",
    "comments": "UserComment",
    "valid_id": "ValidID",
    "pw": "UserPassword",
}

# CustomerCompany Map → DB column.
_CUSTOMER_COMPANY_COL_TO_TAG: dict[str, str] = {
    "customer_id": "CustomerID",
    "name": "CustomerCompanyName",
    "street": "CustomerCompanyStreet",
    "zip": "CustomerCompanyZIP",
    "city": "CustomerCompanyCity",
    "country": "CustomerCompanyCountry",
    "url": "CustomerCompanyURL",
    "comments": "CustomerCompanyComment",
    "valid_id": "ValidID",
}

# Agent / owner / responsible (users table) Map.
_AGENT_COL_TO_TAG: dict[str, str] = {
    "id": "UserID",
    "login": "UserLogin",
    "title": "UserTitle",
    "first_name": "UserFirstname",
    "last_name": "UserLastname",
    "valid_id": "ValidID",
}


@dataclass
class PlaceholderContext:
    """Resolved replacement maps (keys stored lowercased for Znuny-like lookup)."""

    ticket: dict[str, str] = field(default_factory=dict)
    queue: dict[str, str] = field(default_factory=dict)
    owner: dict[str, str] = field(default_factory=dict)
    responsible: dict[str, str] = field(default_factory=dict)
    current_user: dict[str, str] = field(default_factory=dict)
    customer: dict[str, str] = field(default_factory=dict)
    # Configured tiqora_queue_variable rows (name.lower → value); queue-specific
    # overrides global (queue_id IS NULL).
    queue_vars: dict[str, str] = field(default_factory=dict)
    # When set (allow-list gate ON), only these tag names resolve for
    # CUSTOMER_DATA_*. None means gate off (all columns resolve as before).
    customer_allowlist: set[str] | None = None
    queue_name: str = ""
    customer_subject: str = ""
    customer_email_lines: list[str] = field(default_factory=list)


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, bytes | bytearray | memoryview):
        try:
            return bytes(value).decode("utf-8", errors="replace")
        except Exception:
            return ""
    return str(value)


def _lower_map(data: dict[str, str]) -> dict[str, str]:
    return {str(k).lower(): v for k, v in data.items()}


def _lookup(data: dict[str, str], field: str) -> str | None:
    """Return value for *field* (case-insensitive) or None if the key is absent."""
    if not field:
        return None
    key = field.lower()
    if key in data:
        return data[key]
    return None


def _row_to_maps(
    row: dict[str, Any],
    col_to_tag: dict[str, str],
) -> dict[str, str]:
    """Build a lowercased tag map from a DB row: mapped Znuny names + raw columns."""
    out: dict[str, str] = {}
    for col, value in row.items():
        col_s = str(col)
        sval = _as_str(value)
        out[col_s.lower()] = sval
        mapped = col_to_tag.get(col_s) or col_to_tag.get(col_s.lower())
        if mapped:
            out[mapped.lower()] = sval
    return out


def _agent_maps_from_row(row: dict[str, Any] | None) -> dict[str, str]:
    if not row:
        return {}
    data = _row_to_maps(row, _AGENT_COL_TO_TAG)
    first = data.get("userfirstname") or data.get("first_name") or ""
    last = data.get("userlastname") or data.get("last_name") or ""
    full = f"{first} {last}".strip()
    if full:
        data["userfullname"] = full
    if "userid" not in data and "id" in data:
        data["userid"] = data["id"]
    return data


async def _fetch_user_row(session: AsyncSession, user_id: int | None) -> dict[str, Any] | None:
    if not user_id:
        return None
    result = await session.execute(
        text(
            "SELECT id, login, title, first_name, last_name, valid_id"
            " FROM users WHERE id = :uid LIMIT 1"
        ),
        {"uid": int(user_id)},
    )
    mapping = result.mappings().first()
    return dict(mapping) if mapping is not None else None


async def _fetch_customer_maps(
    session: AsyncSession,
    *,
    customer_user_id: str | None,
    customer_id: str | None,
) -> dict[str, str]:
    """Merge customer_company then customer_user (Znuny order: company under user).

    Uses ``SELECT *`` so site-specific columns (e.g. ``wpnum``) are available as
    both raw column names and any standard User* aliases.
    """
    company: dict[str, str] = {}
    user: dict[str, str] = {}

    login = (customer_user_id or "").strip() or None
    cid = (customer_id or "").strip() or None

    if login:
        result = await session.execute(
            text("SELECT * FROM customer_user WHERE login = :login LIMIT 1"),
            {"login": login},
        )
        mapping = result.mappings().first()
        if mapping is not None:
            user = _row_to_maps(dict(mapping), _CUSTOMER_USER_COL_TO_TAG)
            if not cid:
                cid = (user.get("usercustomerid") or user.get("customer_id") or "").strip() or None
            first = user.get("userfirstname") or user.get("first_name") or ""
            last = user.get("userlastname") or user.get("last_name") or ""
            full = f"{first} {last}".strip()
            if full:
                user["userfullname"] = full
            # Znuny compat: UserID aliases UserLogin for customer users.
            if "userid" not in user and "userlogin" in user:
                user["userid"] = user["userlogin"]

    if cid:
        result = await session.execute(
            text("SELECT * FROM customer_company WHERE customer_id = :cid LIMIT 1"),
            {"cid": cid},
        )
        mapping = result.mappings().first()
        if mapping is not None:
            company = _row_to_maps(dict(mapping), _CUSTOMER_COMPANY_COL_TO_TAG)
            if "validid" in company:
                company["customercompanyvalidid"] = company["validid"]

    # Company first, customer_user overwrites on key clash (Znuny return order).
    merged = {**company, **user}
    return merged


async def _fetch_queue_maps(session: AsyncSession, queue_id: int | None) -> dict[str, str]:
    if not queue_id:
        return {}
    # SELECT q.* so site-specific queue columns (e.g. domain, phonenumber from a
    # Znuny patch) are available as <OTRS_QUEUE_...>. Safe on a default Znuny DB:
    # only existing columns are returned. system_address fields stay aliased.
    result = await session.execute(
        text(
            "SELECT q.*,"
            " sa.value0 AS email, sa.value1 AS real_name"
            " FROM queue q"
            " LEFT JOIN system_address sa ON sa.id = q.system_address_id"
            " WHERE q.id = :qid LIMIT 1"
        ),
        {"qid": int(queue_id)},
    )
    mapping = result.mappings().first()
    if mapping is None:
        return {}
    row = dict(mapping)
    data: dict[str, str] = {}
    # Raw columns + Znuny QueueGet-style names.
    aliases = {
        "id": ("QueueID", "ID"),
        "name": ("Name",),
        "group_id": ("GroupID",),
        "unlock_timeout": ("UnlockTimeout",),
        "first_response_time": ("FirstResponseTime",),
        "first_response_notify": ("FirstResponseNotify",),
        "update_time": ("UpdateTime",),
        "update_notify": ("UpdateNotify",),
        "solution_time": ("SolutionTime",),
        "solution_notify": ("SolutionNotify",),
        "system_address_id": ("SystemAddressID",),
        "calendar_name": ("Calendar",),
        "default_sign_key": ("DefaultSignKey",),
        "salutation_id": ("SalutationID",),
        "signature_id": ("SignatureID",),
        "follow_up_id": ("FollowUpID",),
        "follow_up_lock": ("FollowUpLock",),
        "comments": ("Comment", "Comments"),
        "valid_id": ("ValidID",),
        "email": ("Email",),
        "real_name": ("RealName",),
    }
    for col, value in row.items():
        col_s = str(col)
        sval = _as_str(value)
        data[col_s.lower()] = sval
        # Match aliases case-insensitively (drivers may return mixed case).
        for alias in aliases.get(col_s, ()) or aliases.get(col_s.lower(), ()):
            data[alias.lower()] = sval
    return data


async def _fetch_queue_variables(session: AsyncSession, queue_id: int | None) -> dict[str, str]:
    """Load configured queue variables; queue-specific rows override globals.

    Defensive: any error (missing table on a partial deploy, etc.) yields ``{}``
    so placeholder expansion never fails because of this optional feature.
    """
    try:
        if queue_id is not None:
            result = await session.execute(
                text(
                    "SELECT name, value, queue_id FROM tiqora_queue_variable"
                    " WHERE queue_id = :qid OR queue_id IS NULL"
                ),
                {"qid": int(queue_id)},
            )
        else:
            result = await session.execute(
                text(
                    "SELECT name, value, queue_id FROM tiqora_queue_variable WHERE queue_id IS NULL"
                ),
            )
        globals_map: dict[str, str] = {}
        specific: dict[str, str] = {}
        for row in result.mappings().all():
            name = str(row["name"]).lower()
            val = _as_str(row["value"])
            if row["queue_id"] is None:
                globals_map[name] = val
            else:
                specific[name] = val
        return {**globals_map, **specific}
    except Exception:
        logger.debug("placeholder_queue_vars_load_failed", exc_info=True)
        return {}


async def _fetch_customer_allowlist(session: AsyncSession) -> set[str] | None:
    """Return enabled tag names when the allow-list gate is ON; else None.

    None means the gate is off (default) — all customer columns resolve as
    before. An empty set means the gate is on with no registered tags.
    """
    try:
        enabled = await get_setting_bool(session, KEY_CUSTOMER_ALLOWLIST, False)
        if not enabled:
            return None
        result = await session.execute(
            text(
                "SELECT tag_name FROM tiqora_placeholder_field"
                " WHERE enabled"
                " AND source_table IN ('customer_user', 'customer_company')"
            ),
        )
        return {str(row[0]).lower() for row in result.all()}
    except Exception:
        logger.debug("placeholder_customer_allowlist_load_failed", exc_info=True)
        return None


async def load_placeholder_context(
    session: AsyncSession,
    *,
    ticket_id: int | None = None,
    user_id: int | None = None,
    ticket: dict[str, str] | None = None,
    queue_name: str = "",
    customer_subject: str = "",
    customer_email_lines: list[str] | None = None,
) -> PlaceholderContext:
    """Load replacement maps for a ticket (and optional acting agent)."""
    ctx = PlaceholderContext(
        queue_name=queue_name or "",
        customer_subject=customer_subject or "",
        customer_email_lines=list(customer_email_lines or []),
    )
    # Seed with any caller-provided ticket vars (autoresponse / notifications).
    if ticket:
        ctx.ticket = _lower_map({k: _as_str(v) for k, v in ticket.items()})

    if ticket_id is None or session is None:
        if queue_name and "queue" not in ctx.ticket:
            ctx.ticket["queue"] = queue_name
        ctx.current_user = _agent_maps_from_row(
            await _fetch_user_row(session, user_id) if session is not None and user_id else None
        )
        return ctx

    result = await session.execute(
        text(
            "SELECT t.id, t.tn, t.title, t.queue_id, t.user_id, t.responsible_user_id,"
            " t.ticket_priority_id, t.ticket_state_id, t.customer_id, t.customer_user_id,"
            " t.type_id, t.service_id, t.sla_id, t.ticket_lock_id, t.archive_flag,"
            " t.create_time, t.change_time, t.create_by, t.change_by,"
            " q.name AS queue_name,"
            " ts.name AS state_name,"
            " tp.name AS priority_name,"
            " tl.name AS lock_name,"
            " tt.name AS type_name"
            " FROM ticket t"
            " LEFT JOIN queue q ON q.id = t.queue_id"
            " LEFT JOIN ticket_state ts ON ts.id = t.ticket_state_id"
            " LEFT JOIN ticket_priority tp ON tp.id = t.ticket_priority_id"
            " LEFT JOIN ticket_lock_type tl ON tl.id = t.ticket_lock_id"
            " LEFT JOIN ticket_type tt ON tt.id = t.type_id"
            " WHERE t.id = :tid LIMIT 1"
        ),
        {"tid": int(ticket_id)},
    )
    mapping = result.mappings().first()
    if mapping is None:
        ctx.current_user = _agent_maps_from_row(await _fetch_user_row(session, user_id))
        return ctx

    row = dict(mapping)
    owner_id = row.get("user_id")
    responsible_id = row.get("responsible_user_id")
    queue_id = row.get("queue_id")
    cuid = row.get("customer_user_id")
    cid = row.get("customer_id")

    owner_row = await _fetch_user_row(session, int(owner_id) if owner_id else None)
    resp_row = await _fetch_user_row(session, int(responsible_id) if responsible_id else None)
    current_row = await _fetch_user_row(session, user_id) if user_id else owner_row

    ctx.owner = _agent_maps_from_row(owner_row)
    ctx.responsible = _agent_maps_from_row(resp_row)
    ctx.current_user = _agent_maps_from_row(current_row)
    ctx.queue = await _fetch_queue_maps(session, int(queue_id) if queue_id else None)
    ctx.queue_vars = await _fetch_queue_variables(session, int(queue_id) if queue_id else None)
    ctx.customer = await _fetch_customer_maps(
        session,
        customer_user_id=str(cuid) if cuid else None,
        customer_id=str(cid) if cid else None,
    )
    ctx.customer_allowlist = await _fetch_customer_allowlist(session)

    qname = _as_str(row.get("queue_name")) or ctx.queue.get("name") or queue_name or ""
    ctx.queue_name = qname

    ticket_map: dict[str, str] = {
        "id": _as_str(row.get("id")),
        "ticketid": _as_str(row.get("id")),
        "tn": _as_str(row.get("tn")),
        "ticketnumber": _as_str(row.get("tn")),
        "number": _as_str(row.get("tn")),
        "title": _as_str(row.get("title")),
        "queueid": _as_str(row.get("queue_id")),
        "queue": qname,
        "ownerid": _as_str(row.get("user_id")),
        "owner": ctx.owner.get("userfullname") or ctx.owner.get("userlogin") or "",
        "responsibleid": _as_str(row.get("responsible_user_id")),
        "responsible": (
            ctx.responsible.get("userfullname") or ctx.responsible.get("userlogin") or ""
        ),
        "priorityid": _as_str(row.get("ticket_priority_id")),
        "priority": _as_str(row.get("priority_name")),
        "stateid": _as_str(row.get("ticket_state_id")),
        "state": _as_str(row.get("state_name")),
        "lockid": _as_str(row.get("ticket_lock_id")),
        "lock": _as_str(row.get("lock_name")),
        "typeid": _as_str(row.get("type_id")),
        "type": _as_str(row.get("type_name")),
        "serviceid": _as_str(row.get("service_id")),
        "slaid": _as_str(row.get("sla_id")),
        "customerid": _as_str(row.get("customer_id")),
        "customeruserid": _as_str(row.get("customer_user_id")),
        "archiveflag": _as_str(row.get("archive_flag")),
        "created": _as_str(row.get("create_time")),
        "changed": _as_str(row.get("change_time")),
        "createby": _as_str(row.get("create_by")),
        "changeby": _as_str(row.get("change_by")),
        "createtime": _as_str(row.get("create_time")),
        "changetime": _as_str(row.get("change_time")),
    }
    # Raw column names for OTRS_TICKET_tn / title etc.
    for col in (
        "id",
        "tn",
        "title",
        "queue_id",
        "user_id",
        "responsible_user_id",
        "customer_id",
        "customer_user_id",
    ):
        if col in row:
            ticket_map[col] = _as_str(row.get(col))

    # Caller-supplied ticket vars win for explicit keys (autoresponse overrides).
    if ticket:
        ticket_map.update(_lower_map({k: _as_str(v) for k, v in ticket.items()}))
    ctx.ticket = ticket_map
    return ctx


async def _resolve_tag(
    tag: str,
    bracket: str | None,
    *,
    ctx: PlaceholderContext,
    sysconfig: SysConfig,
) -> str:
    """Resolve one OTRS tag (without angle brackets / OTRS_ prefix already stripped)."""
    # Preserve original case for CONFIG_ keys (sysconfig names use :: from _).
    tag_u = tag.upper()

    # Longer / more specific prefixes first (TICKET_OWNER before TICKET_, etc.).
    if tag_u.startswith("TICKET_OWNER_"):
        field = tag[len("TICKET_OWNER_") :]
        found = _lookup(ctx.owner, field)
        return "" if found is None else found

    if tag_u.startswith("TICKET_RESPONSIBLE_"):
        field = tag[len("TICKET_RESPONSIBLE_") :]
        found = _lookup(ctx.responsible, field)
        return "" if found is None else found

    if tag_u.startswith("TICKET_"):
        field = tag[len("TICKET_") :]
        found = _lookup(ctx.ticket, field)
        return "" if found is None else found

    if tag_u.startswith("CUSTOMER_DATA_"):
        field = tag[len("CUSTOMER_DATA_") :]
        # Optional allow-list: when set, only registered+enabled tags resolve.
        if ctx.customer_allowlist is not None and field.lower() not in ctx.customer_allowlist:
            return ""
        found = _lookup(ctx.customer, field)
        return "" if found is None else found

    if tag_u.startswith("CUSTOMER_"):
        rest = tag[len("CUSTOMER_") :]
        rest_u = rest.upper()
        if rest_u == "SUBJECT" or rest_u.startswith("SUBJECT"):
            # CUSTOMER_SUBJECT[n] → first n chars of subject
            subj = ctx.customer_subject or ""
            if bracket and bracket.isdigit():
                return subj[: int(bracket)]
            return subj
        if rest_u == "EMAIL" or rest_u.startswith("EMAIL"):
            lines = ctx.customer_email_lines
            count = int(bracket) if bracket and bracket.isdigit() else len(lines)
            return "\n".join(lines[:count])
        if rest_u in {"BODY", "NOTE", "REALNAME"}:
            # Article body snippets need article context; leave empty rather than raw.
            if rest_u == "REALNAME":
                full = ctx.customer.get("userfullname") or ""
                if not full:
                    full = (
                        f"{ctx.customer.get('userfirstname', '')} "
                        f"{ctx.customer.get('userlastname', '')}"
                    ).strip()
                return full or ctx.customer.get("userlogin") or ""
            return ""
        found = _lookup(ctx.customer, rest)
        return "" if found is None else found

    if tag_u.startswith("OWNER_"):
        field = tag[len("OWNER_") :]
        found = _lookup(ctx.owner, field)
        return "" if found is None else found

    if tag_u.startswith("RESPONSIBLE_"):
        field = tag[len("RESPONSIBLE_") :]
        found = _lookup(ctx.responsible, field)
        return "" if found is None else found

    if tag_u.startswith("CURRENT_"):
        field = tag[len("CURRENT_") :]
        found = _lookup(ctx.current_user, field)
        return "" if found is None else found

    if tag_u.startswith("AGENT_"):
        field = tag[len("AGENT_") :]
        field_u = field.upper()
        # Agent article subject/body need article context — empty when absent.
        if field_u in {"SUBJECT", "BODY", "NOTE", "EMAIL"}:
            return ""
        found = _lookup(ctx.current_user, field)
        return "" if found is None else found

    if tag_u == "QUEUE":
        return ctx.queue_name or _lookup(ctx.queue, "Name") or _lookup(ctx.queue, "name") or ""

    if tag_u.startswith("QUEUE_"):
        field = tag[len("QUEUE_") :]
        # Configured variable → physical queue column → empty.
        configured = ctx.queue_vars.get(field.lower())
        if configured is not None:
            return configured
        found = _lookup(ctx.queue, field)
        if found is None:
            # Unknown queue field (e.g. Domain) — Znuny would yield empty/'-'; log it.
            logger.info(
                "placeholder_unresolved_queue_field",
                field=field,
                tag=f"OTRS_QUEUE_{field}",
            )
            return ""
        return found

    if tag_u.startswith("CONFIG_"):
        setting_name = tag[len("CONFIG_") :].replace("_", "::")
        value = await sysconfig.get(setting_name)
        return "" if value is None else str(value)

    if tag_u in {"EMAIL_DATE", "EMAILDATE"}:
        # Lightweight stand-in for OTRS_EMAIL_DATE (no timezone param support).
        return datetime.now(UTC).strftime("%A, %B %d, %Y at %H:%M:%S (UTC)")

    if tag_u == "FIRST_NAME":
        return ctx.current_user.get("userfirstname") or ""
    if tag_u == "LAST_NAME":
        return ctx.current_user.get("userlastname") or ""

    # Completely unknown tag prefix/name → empty (do not leave raw markup).
    logger.debug("placeholder_unknown_tag", tag=tag)
    return ""


async def expand_placeholders(
    session: AsyncSession | None,
    sysconfig: SysConfig,
    text: str,
    *,
    ticket: dict[str, str] | None = None,
    queue_name: str = "",
    customer_subject: str = "",
    customer_email_lines: list[str] | None = None,
    ticket_id: int | None = None,
    user_id: int | None = None,
    context: PlaceholderContext | None = None,
) -> str:
    """Expand supported ``<OTRS_...>`` tags in *text*.

    When *ticket_id* is set and *session* is provided, ticket / queue / owner /
    responsible / customer maps are loaded from the database. Unknown tags are
    replaced with ``""``. Any unexpected error returns *text* unchanged and logs
    a warning so callers (template listing, reply send) never fail.
    """
    if not text:
        return text
    lowered = text.lower()
    if "<otrs_" not in lowered and "<tiqora_" not in lowered:
        return text

    try:
        return await _expand_placeholders_inner(
            session,
            sysconfig,
            text,
            ticket=ticket,
            queue_name=queue_name,
            customer_subject=customer_subject,
            customer_email_lines=customer_email_lines,
            ticket_id=ticket_id,
            user_id=user_id,
            context=context,
        )
    except Exception:
        logger.warning(
            "placeholder_expansion_failed",
            ticket_id=ticket_id,
            user_id=user_id,
            exc_info=True,
        )
        return text


async def _expand_placeholders_inner(
    session: AsyncSession | None,
    sysconfig: SysConfig,
    text: str,
    *,
    ticket: dict[str, str] | None,
    queue_name: str,
    customer_subject: str,
    customer_email_lines: list[str] | None,
    ticket_id: int | None,
    user_id: int | None,
    context: PlaceholderContext | None,
) -> str:
    if context is not None:
        ctx = context
    elif session is not None and ticket_id is not None:
        ctx = await load_placeholder_context(
            session,
            ticket_id=ticket_id,
            user_id=user_id,
            ticket=ticket,
            queue_name=queue_name,
            customer_subject=customer_subject,
            customer_email_lines=customer_email_lines,
        )
    else:
        ctx = PlaceholderContext(
            ticket=_lower_map({k: _as_str(v) for k, v in (ticket or {}).items()}),
            queue_name=queue_name or "",
            customer_subject=customer_subject or "",
            customer_email_lines=list(customer_email_lines or []),
        )
        if queue_name and "queue" not in ctx.ticket:
            ctx.ticket["queue"] = queue_name
        if session is not None and user_id is not None:
            ctx.current_user = _agent_maps_from_row(await _fetch_user_row(session, user_id))

    result = text
    offset = 0
    for match in list(_TAG_RE.finditer(text)):
        tag = match.group(1)
        bracket = match.group(2)
        replacement = await _resolve_tag(tag, bracket, ctx=ctx, sysconfig=sysconfig)
        start, end = match.span()
        result = result[: start + offset] + replacement + result[end + offset :]
        offset += len(replacement) - (end - start)
    return result


__all__ = [
    "PlaceholderContext",
    "expand_placeholders",
    "load_placeholder_context",
]
