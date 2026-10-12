"""``TicketDeepGet`` and the ``Kernel::System::Util`` helpers of ``Ticket::Generic`` (G9).

Port of ``Kernel/System/Ticket.pm`` ``TicketDeepGet`` (lines 1525-1830),
``_GetBase64EncodedArticleAttachments`` and ``_TicketDeepGetDataCleanUp`` plus
``Kernel/System/Util.pm`` ``DataStructureRemoveElements`` / ``Base64DeepEncode`` of Znuny 6.5.

The ticket and article hashes reuse the wave-1 GenericInterface builders
(:func:`build_ticket_entry`, :func:`build_article_entries`); ``TicketDeepGet`` asks for the flat
``DynamicField_<Name>`` keys instead of the ``DynamicField`` list of the ``TicketGet`` operation,
which is added here. Every other lookup loads one table by key (no joins into ``tiqora_*``).

Deliberate differences (documented, not faked):

* Customer user / company hashes use Znuny's default DB maps (``Kernel/Config/Defaults.pm``
  ``CustomerUser`` / ``CustomerCompany``). Custom column maps, dynamic-field mappings, LDAP
  sources and a second source (``CustomerUser1`` ...) are not read.
* ``GetUserData`` skips the out-of-office and logged-in status messages (they need the session
  table and change only ``UserFullname``/``LoggedStatusMessage``).
* ITSM columns of service / SLA (``IsITSMInstalled``) are not added.
* The keys ``Source``, ``Config`` and ``CompanyConfig`` Znuny adds to customer hashes are
  removed by the clean-up anyway and therefore not built (``Source`` is kept).
"""

from __future__ import annotations

import base64
import copy
import re
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from email.utils import getaddresses
from typing import Any, Final
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.api.compat.ticket_get import (
    TicketGetContext,
    _attachment_entries,
    _df_value,
    _load_attachment_contents,
    _load_attachment_index,
    attachment_index,
    build_article_entries,
    build_ticket_entry,
    load_ticket_get_context,
    znuny_ts,
)
from tiqora.domain.dynamic_field_values import get_df_values_bulk
from tiqora.util.perl import perl_truthy
from tiqora.znuny.sysconfig import SysConfig, decode_effective_value

__all__ = [
    "DEEP_GET_CLEANUP_RE",
    "base64_deep_encode",
    "data_structure_remove_elements",
    "perl_split",
    "ticket_deep_get",
]

#: ``_TicketDeepGetDataCleanUp``: keys of the nested hashes that match are deleted.
DEEP_GET_CLEANUP_RE: Final = re.compile(r"::|config|secret|passw|userpw|auth|token", re.I)

_NESTED_SEP: Final = "->"


# ---------------------------------------------------------------------------
# Kernel::System::Util
# ---------------------------------------------------------------------------


def perl_split(sep: str, value: str) -> list[str]:
    """Perl ``split`` with a literal separator: trailing empty fields are dropped."""
    if value == "":
        return []
    parts = value.split(sep)
    while parts and parts[-1] == "":
        parts.pop()
    return parts


def _remove(data: Any, hash_keys: Sequence[str]) -> None:
    if isinstance(data, list):
        for element in data:
            _remove(element, hash_keys)
        return
    if not isinstance(data, dict) or not hash_keys:
        return
    for nested in hash_keys:
        keys = perl_split(_NESTED_SEP, nested)
        # Znuny keeps looping over the remaining keys of the path on the SAME level after
        # descending - ``Articles->Body`` also deletes a top-level ``Body``. Ported as is.
        while keys:
            key = keys.pop(0)
            if key not in data:
                break
            if not keys:
                del data[key]
                break
            _remove(data[key], [_NESTED_SEP.join(keys)])


def data_structure_remove_elements(data: Any, hash_keys: Sequence[str]) -> None:
    """``DataStructureRemoveElements``: delete ``a->b->c`` paths from nested dicts/lists, in place.

    Lists apply the remaining path to every element; a missing key or a non-container ends the
    path silently.
    """
    _remove(data, list(hash_keys))


def _encode_scalar(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        value = int(value)
    raw = value if isinstance(value, bytes) else str(value).encode("utf-8")
    # ``encode_base64($x)`` with the default eol: lines of 76 characters, each ending in "\n".
    return base64.encodebytes(raw).decode("ascii")


def _b64(data: Any, hash_keys: Sequence[str]) -> Any:
    if isinstance(data, list):
        for index, element in enumerate(data):
            data[index] = _b64(element, hash_keys)
        return data
    if isinstance(data, dict):
        if not hash_keys:
            return data
        for nested in hash_keys:
            keys = perl_split(_NESTED_SEP, nested)
            while keys:
                key = keys.pop(0)
                if key not in data:
                    break
                data[key] = _b64(data[key], [_NESTED_SEP.join(keys)])
        return data
    return _encode_scalar(data)


def base64_deep_encode(data: Any, hash_keys: Sequence[str]) -> None:
    """``Base64DeepEncode``: base64-encode the values at ``a->b`` paths, in place.

    Faithful to the Perl: scalars become MIME base64 with the default line wrapping (newline
    every 76 characters and at the end), the elements of a list under the last key are encoded
    one by one, and a dict under the last key is left untouched.
    """
    _b64(data, list(hash_keys))


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _cleanup(data: dict[str, Any]) -> dict[str, Any]:
    for key in [k for k in data if DEEP_GET_CLEANUP_RE.search(k)]:
        del data[key]
    return data


def _ts(value: Any) -> Any:
    return znuny_ts(value) if isinstance(value, datetime) else value


def _text(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).decode("utf-8", errors="replace")
    return value


def _tz_name(raw: str) -> ZoneInfo:
    try:
        return ZoneInfo(raw or "UTC")
    except (KeyError, ValueError, OSError):
        return ZoneInfo("UTC")


def _stamp(epoch: Any, zone: ZoneInfo) -> str | None:
    try:
        return (
            datetime.fromtimestamp(int(epoch), UTC).astimezone(zone).strftime("%Y-%m-%d %H:%M:%S")
        )
    except (TypeError, ValueError, OverflowError):
        return None


async def _preferences(
    session: AsyncSession, table: str, id_column: str, key: Any
) -> dict[str, Any]:
    rows = (
        await session.execute(
            text(
                f"SELECT preferences_key, preferences_value FROM {table}"  # noqa: S608 - constants
                f" WHERE {id_column} = :id"
            ),
            {"id": key},
        )
    ).fetchall()
    return {str(k): _text(v) for k, v in rows}


async def _pref_groups(session: AsyncSession | None, prefix: str) -> dict[str, Any]:
    """Every ``<prefix>###<Group>`` setting (``PreferencesGroups`` / ``CustomerPreferencesGroups``).

    Znuny stores one setting per group, so ``SysConfig.get(prefix)`` never matches. The latest
    system-wide ``sysconfig_modified`` row wins (``is_valid=0`` disables the group), then
    ``sysconfig_default`` (valid rows only), as in ``SysConfig._fetch_effective_from_db``.
    """
    if session is None:
        return {}
    like = prefix + "###%"
    modified = (
        await session.execute(
            text(
                "SELECT name, is_valid, effective_value FROM sysconfig_modified"
                " WHERE name LIKE :like AND user_id IS NULL ORDER BY id DESC"
            ),
            {"like": like},
        )
    ).fetchall()
    default = (
        await session.execute(
            text(
                "SELECT name, effective_value FROM sysconfig_default"
                " WHERE name LIKE :like AND is_valid = 1"
            ),
            {"like": like},
        )
    ).fetchall()
    raw: dict[str, Any] = {}
    disabled: set[str] = set()
    for name, is_valid, value in modified:
        if name in raw or name in disabled:
            continue
        if int(is_valid) == 1:
            raw[str(name)] = value
        else:
            disabled.add(str(name))
    for name, value in default:
        if name not in raw and name not in disabled:
            raw[str(name)] = value
    return {name.split("###", 1)[1]: decode_effective_value(raw[name]) for name in raw}


async def _apply_pref_defaults(session: AsyncSession, data: dict[str, Any], prefix: str) -> None:
    """``<prefix>###<Group>`` ``DataSelected`` defaults for keys that are not set."""
    groups = await _pref_groups(session, prefix)
    for name in sorted(groups):
        group = groups[name]
        if not isinstance(group, dict) or group.get("DataSelected") is None:
            continue
        pref_key = group.get("PrefKey")
        if isinstance(pref_key, str) and data.get(pref_key) is None:
            data[pref_key] = group["DataSelected"]


# ---------------------------------------------------------------------------
# Lookups (one table per query, always by key)
# ---------------------------------------------------------------------------


def _user_fullname(first: str, last: str, login: str, order: str) -> str | None:
    """``User.pm`` ``_UserFullname`` (undef when first name, last name or login is empty)."""
    if not (first and last and login):
        return None
    forms = {
        "0": f"{first} {last}",
        "1": f"{last}, {first}",
        "2": f"{first} {last} ({login})",
        "3": f"{last}, {first} ({login})",
        "4": f"({login}) {first} {last}",
        "5": f"({login}) {last}, {first}",
        "6": f"{last} {first}",
        "7": f"{last} {first} ({login})",
        "8": f"({login}) {last} {first}",
        "9": f"{last}{first}",
    }
    return forms.get(order, forms["0"])


async def _user_data(
    session: AsyncSession, user_id: int, sysconfig: SysConfig, zone: ZoneInfo
) -> dict[str, Any]:
    """``User.pm`` ``GetUserData(UserID)`` without the out-of-office session lookups."""
    row = (
        (
            await session.execute(
                text(
                    "SELECT id, login, title, first_name, last_name, pw, valid_id, create_time,"
                    " change_time FROM users WHERE id = :id"
                ),
                {"id": user_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        return {}
    data: dict[str, Any] = {
        "UserID": row["id"],
        "UserLogin": row["login"],
        "UserTitle": row["title"],
        "UserFirstname": row["first_name"],
        "UserLastname": row["last_name"],
        "UserPw": row["pw"],
        "ValidID": row["valid_id"],
        "CreateTime": _ts(row["create_time"]),
        "ChangeTime": _ts(row["change_time"]),
    }
    order = str(await sysconfig.get("FirstnameLastnameOrder", 0) or 0)
    data["UserFullname"] = _user_fullname(
        str(row["first_name"] or ""), str(row["last_name"] or ""), str(row["login"] or ""), order
    )
    prefs = await _preferences(session, "user_preferences", "user_id", int(row["id"]))
    if prefs.get("UserLastLogin"):
        stamp = _stamp(prefs["UserLastLogin"], zone)
        if stamp is not None:
            prefs["UserLastLoginTimestamp"] = stamp
    if not prefs.get("UserEmail"):
        prefs["UserEmail"] = data["UserLogin"]
    data.update(prefs)
    await _apply_pref_defaults(session, data, "PreferencesGroups")
    return data


async def _queue_data(session: AsyncSession, queue_id: int) -> dict[str, Any]:
    """``Queue.pm`` ``QueueGet(ID)`` (queue joined to its system address, plus preferences)."""
    row = (
        (
            await session.execute(
                text(
                    "SELECT q.id, q.name, q.group_id, q.unlock_timeout, q.system_address_id,"
                    " q.salutation_id, q.signature_id, q.comments, q.valid_id,"
                    " q.first_response_time, q.first_response_notify, q.update_time,"
                    " q.update_notify, q.solution_time, q.solution_notify, q.follow_up_id,"
                    " q.follow_up_lock, sa.value0 AS email, sa.value1 AS real_name,"
                    " q.default_sign_key, q.calendar_name, q.create_time, q.change_time"
                    " FROM queue q JOIN system_address sa ON sa.id = q.system_address_id"
                    " WHERE q.id = :id"
                ),
                {"id": queue_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        return {}
    data: dict[str, Any] = {
        "QueueID": row["id"],
        "Name": row["name"],
        "GroupID": row["group_id"],
        "UnlockTimeout": row["unlock_timeout"],
        "FirstResponseTime": row["first_response_time"],
        "FirstResponseNotify": row["first_response_notify"],
        "UpdateTime": row["update_time"],
        "UpdateNotify": row["update_notify"],
        "SolutionTime": row["solution_time"],
        "SolutionNotify": row["solution_notify"],
        "FollowUpID": row["follow_up_id"],
        "FollowUpLock": row["follow_up_lock"],
        "SystemAddressID": row["system_address_id"],
        "SalutationID": row["salutation_id"],
        "SignatureID": row["signature_id"],
        "Comment": row["comments"],
        "ValidID": row["valid_id"],
        "Email": row["email"],
        "RealName": row["real_name"],
        "DefaultSignKey": row["default_sign_key"],
        "Calendar": row["calendar_name"] or "",
        "CreateTime": _ts(row["create_time"]),
        "ChangeTime": _ts(row["change_time"]),
    }
    data.update(await _preferences(session, "queue_preferences", "queue_id", queue_id))
    return data


async def _simple_row(session: AsyncSession, table: str, row_id: int) -> dict[str, Any]:
    """``TypeGet`` / ``PriorityGet``: id, name, valid and the audit columns."""
    row = (
        (
            await session.execute(
                text(
                    "SELECT id, name, valid_id, create_time, create_by, change_time, change_by"
                    f" FROM {table} WHERE id = :id"  # noqa: S608 - constant table name
                ),
                {"id": row_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        return {}
    return {
        "ID": row["id"],
        "Name": row["name"],
        "ValidID": row["valid_id"],
        "CreateTime": _ts(row["create_time"]),
        "CreateBy": row["create_by"],
        "ChangeTime": _ts(row["change_time"]),
        "ChangeBy": row["change_by"],
    }


async def _service_data(session: AsyncSession, service_id: int) -> dict[str, Any]:
    """``Service.pm`` ``ServiceGet(ServiceID)`` (no ITSM columns)."""
    row = (
        (
            await session.execute(
                text(
                    "SELECT id, name, valid_id, comments, create_time, create_by, change_time,"
                    " change_by FROM service WHERE id = :id"
                ),
                {"id": service_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        return {}
    name = str(row["name"])
    data: dict[str, Any] = {
        "ServiceID": row["id"],
        "Name": name,
        "ValidID": row["valid_id"],
        "Comment": row["comments"] or "",
        "CreateTime": _ts(row["create_time"]),
        "CreateBy": row["create_by"],
        "ChangeTime": _ts(row["change_time"]),
        "ChangeBy": row["change_by"],
        "NameShort": name,
    }
    match = re.match(r"\A(.*)::(.+?)\Z", name, re.S)
    if match:
        data["NameShort"] = match.group(2)
        parent = (
            await session.execute(
                text("SELECT id FROM service WHERE name = :n"), {"n": match.group(1)}
            )
        ).scalar_one_or_none()
        data["ParentID"] = parent
    data.update(await _preferences(session, "service_preferences", "service_id", service_id))
    return data


async def _sla_data(session: AsyncSession, sla_id: int) -> dict[str, Any]:
    """``SLA.pm`` ``SLAGet(SLAID)`` (no ITSM columns)."""
    row = (
        (
            await session.execute(
                text(
                    "SELECT id, name, calendar_name, first_response_time, first_response_notify,"
                    " update_time, update_notify, solution_time, solution_notify, valid_id,"
                    " comments, create_time, create_by, change_time, change_by"
                    " FROM sla WHERE id = :id"
                ),
                {"id": sla_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        return {}
    data: dict[str, Any] = {
        "SLAID": row["id"],
        "Name": row["name"],
        "Calendar": row["calendar_name"] or "",
        "FirstResponseTime": row["first_response_time"],
        "FirstResponseNotify": row["first_response_notify"],
        "UpdateTime": row["update_time"],
        "UpdateNotify": row["update_notify"],
        "SolutionTime": row["solution_time"],
        "SolutionNotify": row["solution_notify"],
        "ValidID": row["valid_id"],
        "Comment": row["comments"] or "",
        "CreateTime": _ts(row["create_time"]),
        "CreateBy": row["create_by"],
        "ChangeTime": _ts(row["change_time"]),
        "ChangeBy": row["change_by"],
    }
    service_ids = (
        await session.execute(
            text("SELECT service_id FROM service_sla WHERE sla_id = :id ORDER BY service_id"),
            {"id": sla_id},
        )
    ).fetchall()
    data["ServiceIDs"] = [r[0] for r in service_ids]
    data.update(await _preferences(session, "sla_preferences", "sla_id", sla_id))
    return data


async def _company_data(session: AsyncSession, customer_id: str) -> dict[str, Any]:
    """``CustomerCompany.pm`` ``CustomerCompanyGet`` with the default map."""
    row = (
        (
            await session.execute(
                text(
                    "SELECT customer_id, name, street, zip, city, country, url, comments,"
                    " valid_id, create_time, create_by, change_time, change_by"
                    " FROM customer_company WHERE LOWER(customer_id) = LOWER(:cid)"
                ),
                {"cid": customer_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        return {}
    return {
        "CustomerID": row["customer_id"],
        "CustomerCompanyName": row["name"],
        "CustomerCompanyStreet": row["street"],
        "CustomerCompanyZIP": row["zip"],
        "CustomerCompanyCity": row["city"],
        "CustomerCompanyCountry": row["country"],
        "CustomerCompanyURL": row["url"],
        "CustomerCompanyComment": row["comments"],
        "ValidID": row["valid_id"],
        "CreateTime": _ts(row["create_time"]),
        "CreateBy": row["create_by"],
        "ChangeTime": _ts(row["change_time"]),
        "ChangeBy": row["change_by"],
        "Source": "CustomerCompany",
    }


async def _customer_user_data(
    session: AsyncSession, login: str, sysconfig: SysConfig, zone: ZoneInfo
) -> dict[str, Any]:
    """``CustomerUser.pm`` ``CustomerUserDataGet(User)``: default map, company support on."""
    row = (
        (
            await session.execute(
                text(
                    "SELECT login, email, customer_id, pw, title, first_name, last_name, phone,"
                    " fax, mobile, street, zip, city, country, comments, valid_id, create_time,"
                    " create_by, change_time, change_by FROM customer_user"
                    " WHERE LOWER(login) = LOWER(:login)"
                ),
                {"login": login},
            )
        )
        .mappings()
        .first()
    )
    if row is None or not row["login"]:
        return {}
    customer: dict[str, Any] = {
        "UserTitle": row["title"],
        "UserFirstname": row["first_name"],
        "UserLastname": row["last_name"],
        "UserLogin": row["login"],
        "UserPassword": row["pw"],
        "UserEmail": row["email"],
        "UserCustomerID": row["customer_id"],
        "UserPhone": row["phone"],
        "UserFax": row["fax"],
        "UserMobile": row["mobile"],
        "UserStreet": row["street"],
        "UserZip": row["zip"],
        "UserCity": row["city"],
        "UserCountry": row["country"],
        "UserComment": row["comments"],
        "ValidID": row["valid_id"],
        "CreateTime": _ts(row["create_time"]),
        "CreateBy": row["create_by"],
        "ChangeTime": _ts(row["change_time"]),
        "ChangeBy": row["change_by"],
    }
    # CustomerUserListFields default: first_name, last_name, email.
    parts = [
        str(customer[k]) for k in ("UserFirstname", "UserLastname", "UserEmail") if customer[k]
    ]
    mail_string = re.sub(r"^(.*)\s(.+?@.+?\..+?)(\s|)$", r'"\1" <\2>', " ".join(parts))
    customer["UserMailString"] = mail_string
    customer["UserID"] = customer["UserLogin"]
    prefs = await _preferences(session, "customer_preferences", "user_id", customer["UserID"])
    if prefs.get("UserLastLogin"):
        stamp = _stamp(prefs["UserLastLogin"], zone)
        if stamp is not None:
            prefs["UserLastLoginTimestamp"] = stamp
    customer.update(prefs)

    # CustomerUser.pm: name from CustomerUserNameFields (title, first_name, last_name)
    name_parts = [
        str(customer[k]) for k in ("UserTitle", "UserFirstname", "UserLastname") if customer[k]
    ]
    customer["UserFullname"] = " ".join(name_parts)
    await _apply_pref_defaults(session, customer, "CustomerPreferencesGroups")

    company: dict[str, Any] = {}
    if perl_truthy(await sysconfig.get("CustomerCompany", True)):
        company = await _company_data(session, str(customer["UserCustomerID"] or ""))
        company["CustomerCompanyValidID"] = company.get("ValidID")
    return {**company, **customer, "Source": "CustomerUser"}


# ---------------------------------------------------------------------------
# Articles
# ---------------------------------------------------------------------------


def _real_names(value: str) -> str:
    """``FromRealname`` & co: the display name of every address, else the address itself."""
    if "@" not in value:
        return value
    names = [name or addr for name, addr in getaddresses([value])]
    return ", ".join(n for n in names if n)


async def _attachments_base64(
    session: AsyncSession, article_ids: Sequence[int]
) -> dict[int, list[dict[str, Any]]]:
    """``_GetBase64EncodedArticleAttachments`` for many articles (nothing excluded)."""
    if not article_ids:
        return {}
    index, db_ids = await _load_attachment_index(session, article_ids)
    contents = await _load_attachment_contents(session, list(db_ids.values()))
    out: dict[int, list[dict[str, Any]]] = {}
    for article_id, rows in index.items():
        every = attachment_index(
            [replace(r, content=contents.get(db_ids[(article_id, r.file_id)])) for r in rows],
            exclude_plain_text=False,
            exclude_html_body=False,
            exclude_inline=False,
            html_body=None,
        )
        entries = _attachment_entries(every, with_contents=True)
        if entries:
            out[article_id] = entries
    return out


async def _articles(
    session: AsyncSession,
    ticket_id: int,
    ctx: TicketGetContext,
    *,
    all_attachments: bool,
) -> list[dict[str, Any]]:
    entries = await build_article_entries(
        session,
        ticket_id,
        customer_only=False,
        sender_types=None,
        order="ASC",
        limit=0,
        dynamic_fields=False,
        attachments=False,
        get_attachment_contents=False,
        html_body_as_attachment=True,
        ctx=ctx,
    )
    if not entries:
        return []
    ids = [int(e["ArticleID"]) for e in entries]
    values = await get_df_values_bulk(session, ctx.article_fields, ids)
    attachments = await _attachments_base64(session, ids) if all_attachments else {}
    out: list[dict[str, Any]] = []
    for entry in entries:
        article_id = int(entry["ArticleID"])
        # TimeUnit is added by the TicketGet *operation* only, not by ArticleGet
        entry.pop("TimeUnit", None)
        # ArticleGet(RealNames => 1)
        for key in ("From", "To", "Cc", "Bcc"):
            value = entry.get(key)
            if value and perl_truthy(value):
                entry[f"{key}Realname"] = _real_names(str(value))
        # _MetaArticleDynamicFieldsGet: every Article field with a config, undef when unset
        for field in ctx.article_fields:
            pair = (field.id, article_id)
            entry[f"DynamicField_{field.name}"] = (
                _df_value(field, values[pair]) if pair in values else None
            )
        if article_id in attachments:
            entry["Attachment"] = attachments[article_id]
        out.append(_cleanup(entry))
    return out


# ---------------------------------------------------------------------------
# TicketDeepGet
# ---------------------------------------------------------------------------


async def ticket_deep_get(
    session: AsyncSession,
    ticket_id: int,
    *,
    article_id: int | None,
    all_attachments: bool,
    sysconfig: SysConfig,
) -> dict[str, Any] | None:
    """``TicketDeepGet``: ticket, all articles and every related object, cleaned of secrets.

    ``None`` when the ticket does not exist (Znuny returns an empty hash). ``Article`` is ``{}``
    unless the ticket has exactly one article or *article_id* names exactly one of them; that
    article is then returned with all its attachments (base64).
    """
    ctx = await load_ticket_get_context(session, dynamic_fields=True, articles=True)
    now = datetime.now(UTC).replace(tzinfo=None)
    ticket = await build_ticket_entry(
        session, ticket_id, dynamic_fields=False, extended=True, now=now, ctx=ctx
    )
    if ticket is None:
        return None
    # TicketGet(DynamicFields => 1): flat DynamicField_<Name> keys, only fields with a value row.
    values = await get_df_values_bulk(session, ctx.ticket_fields, [ticket_id])
    for field in ctx.ticket_fields:
        if (field.id, ticket_id) in values:
            ticket[f"DynamicField_{field.name}"] = _df_value(field, values[(field.id, ticket_id)])

    data: dict[str, Any] = dict(ticket)
    zone = _tz_name(await sysconfig.otrs_time_zone())
    data["Articles"] = await _articles(session, ticket_id, ctx, all_attachments=all_attachments)

    customer_user = str(ticket.get("CustomerUserID") or "")
    data["CustomerUser"] = (
        _cleanup(await _customer_user_data(session, customer_user, sysconfig, zone))
        if customer_user
        else {}
    )
    customer_id = str(ticket.get("CustomerID") or "")
    data["CustomerCompany"] = (
        _cleanup(await _company_data(session, customer_id)) if customer_id else {}
    )
    data["QueueData"] = (
        _cleanup(await _queue_data(session, int(ticket["QueueID"]))) if ticket["QueueID"] else {}
    )
    data["TypeData"] = (
        _cleanup(await _simple_row(session, "ticket_type", int(ticket["TypeID"])))
        if ticket["TypeID"]
        else {}
    )
    data["PriorityData"] = (
        _cleanup(await _simple_row(session, "ticket_priority", int(ticket["PriorityID"])))
        if ticket["PriorityID"]
        else {}
    )
    data["ServiceData"] = (
        _cleanup(await _service_data(session, int(ticket["ServiceID"])))
        if ticket["ServiceID"]
        else {}
    )
    data["SLAData"] = (
        _cleanup(await _sla_data(session, int(ticket["SLAID"]))) if ticket["SLAID"] else {}
    )
    for key, id_key in (
        ("OwnerData", "OwnerID"),
        ("ResponsibleData", "ResponsibleID"),
        ("CreateByData", "CreateBy"),
    ):
        user_id = ticket.get(id_key)
        data[key] = (
            _cleanup(await _user_data(session, int(user_id), sysconfig, zone)) if user_id else {}
        )

    data["Article"] = {}
    articles = data["Articles"]
    if not articles:
        return data
    # One article: returned regardless of the given id (valid or not).
    selected = int(articles[0]["ArticleID"]) if len(articles) == 1 else article_id
    if not selected:
        return data
    matches = [a for a in articles if int(a["ArticleID"]) == int(selected)]
    if len(matches) != 1:
        return data
    article = copy.deepcopy(matches[0])
    attachments = (await _attachments_base64(session, [int(selected)])).get(int(selected))
    if attachments:
        article["Attachment"] = attachments
    data["Article"] = article
    return data
