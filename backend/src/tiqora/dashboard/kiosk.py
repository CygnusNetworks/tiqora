"""Wallboard links (kiosk): token, store and the pure redaction layer (E9).

A link is a long random secret whose sha256 is stored; the data behind it runs as the admin who
created it. The kiosk API is the only unauthenticated surface, so this module is deliberately
conservative: everything that leaves is picked from an allow-list, and a widget type without a
rule is forbidden (fail closed). Logs carry the link id, never the secret.

The redaction half is pure (no I/O); the store half is called by
``api/v1/admin/dashboard_kiosk_links.py`` and, from Task 11 on, by the public ``/kiosk`` API.
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable, Mapping
from datetime import timedelta
from typing import Any, Final

import structlog
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.ai.limits import KIND_PROVIDER_COST
from tiqora.dashboard import store
from tiqora.dashboard.schemas import (
    KioskDashboardRef,
    KioskLinkIn,
    KioskLinkOut,
    WidgetDataOut,
)
from tiqora.db.legacy.user import Users
from tiqora.db.tiqora.models import (
    TiqoraDashboard,
    TiqoraDashboardKioskItem,
    TiqoraDashboardKioskToken,
)
from tiqora.domain.schemas import TicketListItem
from tiqora.jobs.service import utc_now
from tiqora.permissions.engine import PermissionEngine
from tiqora.reporting.person_dimensions import NON_PERSON_DIMENSIONS, PERSON_DIMENSIONS

__all__ = [
    "KIOSK_RULES",
    "KIOSK_SAFE_TICKET_FIELDS",
    "KIOSK_SETTINGS_WHITELIST",
    "MSG_HIDDEN",
    "MSG_NOT_FOUND",
    "NON_PERSON_DIMENSIONS",
    "PERSON_DIMENSIONS",
    "create_link",
    "delete_link",
    "hash_token",
    "kiosk_title",
    "list_links",
    "new_token",
    "redact",
    "redact_result",
    "redact_settings",
    "revoke_link",
]

logger = structlog.get_logger(__name__)

MSG_NOT_FOUND: Final = "Wallboard-Link nicht gefunden."
MSG_HIDDEN: Final = "Auf dem Wallboard ausgeblendet."
MSG_REVOKE_FIRST: Final = "Bitte den Link zuerst widerrufen."
TOKEN_BYTES: Final = 32  # token_urlsafe(32): 43 characters, 256 bit
HINT_LENGTH: Final = 4


# --------------------------------------------------------------------------- token


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token() -> tuple[str, str, str]:
    """``(token, sha256 hex, hint)``; the hint is the token's last four characters."""
    token = secrets.token_urlsafe(TOKEN_BYTES)
    return token, hash_token(token), token[-HINT_LENGTH:]


# --------------------------------------------------------------------------- redaction

#: ``TicketListItem`` fields a wallboard without details may show: no title, customer, owner,
#: sender or link data. Anything not listed here (also fields added later) is dropped.
KIOSK_SAFE_TICKET_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "id",
        "tn",
        "queue_id",
        "queue_name",
        "state_id",
        "state",
        "state_type",
        "priority_id",
        "priority",
        "lock_id",
        "create_time",
        "change_time",
        "age_seconds",
        "until_time",
        "escalation_time",
        "escalation_response_time",
        "escalation_update_time",
        "escalation_solution_time",
    }
)

#: settings keys per widget type the wallboard needs to render it; nothing else leaves (never
#: ``source``, ``report_key``, ``parameters``, notes or links)
KIOSK_SETTINGS_WHITELIST: Final[dict[str, frozenset[str]]] = {
    "core.counter": frozenset({"tone"}),
    "core.ticket-list": frozenset({"columns", "limit", "density", "sort"}),
    "core.queue-overview": frozenset({"limit", "only_with_work"}),
    "core.escalations": frozenset({"limit", "within_hours"}),
    "core.reminders": frozenset({"limit", "window_hours"}),
    "core.calendar-agenda": frozenset({"days", "limit"}),
    "core.team-presence": frozenset({"limit"}),
    "core.report": frozenset({"display", "max_rows"}),
    "core.sla-gauge": frozenset({"target_percent"}),
    "core.backlog-aging": frozenset({"max_rows"}),
    "core.heatmap": frozenset(),
    "core.chat-queue": frozenset({"limit"}),
    "core.ai-status": frozenset(),
}

Rule = Callable[[dict[str, Any], bool], dict[str, Any] | None]


#: neutral values for every sensitive ``TicketListItem`` field (and the required ones, should a
#: source lack them), so a redacted item still satisfies the type the wallboard is written against
_NEUTRAL_TICKET: Final[dict[str, Any]] = {
    "id": 0,
    "tn": "",
    "title": None,
    "queue_id": 0,
    "state_id": 0,
    "priority_id": 0,
    "lock_id": 0,
    "owner_id": 0,
    "owner_login": None,
    "owner_name": None,
    "customer_id": None,
    "customer_user_id": None,
    "customer_email": None,
    "first_from": None,
    "channel": "email",
    "chat_display_name": None,
    "chat_username": None,
    "links": [],
    "create_time": "1970-01-01T00:00:00Z",
    "change_time": "1970-01-01T00:00:00Z",
}


def _ticket(ticket: Mapping[str, Any], show_details: bool) -> dict[str, Any]:
    """Without details: a full ``TicketListItem`` of whitelisted source fields over neutral
    defaults (not a key-deleted dict), so unknown or future fields cannot leave."""
    if show_details:
        return dict(ticket)
    picked = {k: v for k, v in ticket.items() if k in KIOSK_SAFE_TICKET_FIELDS}
    item = TicketListItem.model_validate({**_NEUTRAL_TICKET, **picked})
    return item.model_dump(mode="json")


def _keep(data: dict[str, Any], _show_details: bool) -> dict[str, Any]:
    return data


def _ticket_list(data: dict[str, Any], show_details: bool) -> dict[str, Any]:
    out = dict(data)
    out["items"] = [_ticket(i, show_details) for i in data.get("items", [])]
    return out


def _wrapped_tickets(data: dict[str, Any], show_details: bool) -> dict[str, Any]:
    """Escalations and reminders: items are ``{"ticket": {...}, ...}``."""
    out = dict(data)
    out["items"] = [
        {**item, "ticket": _ticket(item.get("ticket") or {}, show_details)}
        for item in data.get("items", [])
    ]
    return out


def _team_presence(data: dict[str, Any], show_details: bool) -> dict[str, Any]:
    if show_details:
        return data
    online = data.get("online", [])
    counts: dict[str, int] = {}
    for person in online:
        status = str(person.get("status", "available"))
        counts[status] = counts.get(status, 0) + 1
    return {"total_online": data.get("total_online", len(online)), "by_status": counts}


def _chat_queue(data: dict[str, Any], show_details: bool) -> dict[str, Any]:
    out = {k: v for k, v in data.items() if k != "visitor_name"}
    if not show_details:
        out.pop("items", None)
    else:
        out["items"] = [
            {k: v for k, v in item.items() if k != "visitor_name"} for item in data.get("items", [])
        ]
    return out


def _ai_status(data: dict[str, Any], show_details: bool) -> dict[str, Any]:
    out = {k: v for k, v in data.items() if k != "budgets"}
    if show_details:
        # provider cost budgets (money) never leave, queue token budgets may
        out["budgets"] = [
            {k: v for k, v in b.items() if k != "currency"}
            for b in data.get("budgets", [])
            if b.get("kind") != KIND_PROVIDER_COST
        ]
    return out


def _calendar(data: dict[str, Any], show_details: bool) -> dict[str, Any] | None:
    return data if show_details else None


def _report_meta(meta: Mapping[str, Any]) -> dict[str, Any]:
    """A saved report's key is an internal reference and its name may name a customer: keep both
    for built-ins only."""
    if meta.get("kind") == "builtin":
        return dict(meta)
    return {**meta, "key": None, "name": None}


def _report(data: dict[str, Any], show_details: bool) -> dict[str, Any] | None:
    if show_details:
        return data
    out = {k: v for k, v in data.items() if k not in {"definition", "report", "ignored_filters"}}
    if isinstance(data.get("report"), Mapping):
        out["report"] = _report_meta(data["report"])
    definition = data.get("definition")
    if definition is None and data.get("result") is not None:
        return None  # a result without its definition cannot be checked: fail closed
    if definition is not None:
        dims = definition.get("dimensions", [])
        keys = {str(d.get("key")) for d in dims} | {
            str(c) for d in dims for c in d.get("choices", [])
        }
        # a person dimension, and any dimension nobody classified, is hidden (fail closed)
        if keys - NON_PERSON_DIMENSIONS:
            return None
        out["definition"] = {
            k: definition[k]
            for k in ("source", "dimensions", "measures", "visualization")
            if k in definition
        }
        if isinstance(data.get("result"), Mapping):
            out["result"] = {k: v for k, v in data["result"].items() if k != "report_key"}
    return out


#: The one table of widget types that run on a wallboard. A type without an entry is neither
#: run nor shown (``run_batch`` input is filtered by it before any provider executes).
KIOSK_RULES: Final[dict[str, Rule]] = {
    "core.counter": _keep,
    "core.ticket-list": _ticket_list,
    "core.queue-overview": _keep,
    "core.escalations": _wrapped_tickets,
    "core.reminders": _wrapped_tickets,
    "core.team-presence": _team_presence,
    "core.calendar-agenda": _calendar,
    "core.chat-queue": _chat_queue,
    "core.ai-status": _ai_status,
    "core.report": _report,
    "core.sla-gauge": _report,
    "core.backlog-aging": _report,
    "core.heatmap": _report,
}


def _hidden() -> WidgetDataOut:
    return WidgetDataOut(status="forbidden", error=MSG_HIDDEN)


def redact(type_key: str, data: dict[str, Any], show_details: bool) -> WidgetDataOut:
    """The wallboard's view of one successful widget result (whitelist, fail closed)."""
    rule = KIOSK_RULES.get(type_key)
    if rule is None:
        return _hidden()
    out = rule(data, show_details)
    if out is None:
        return _hidden()
    return WidgetDataOut(status="ok", data=out)


def redact_result(type_key: str, result: WidgetDataOut, show_details: bool) -> WidgetDataOut:
    """Like :func:`redact` for a runner result: only ``ok`` carries data to redact."""
    if type_key not in KIOSK_RULES:
        return _hidden()
    if result.status != "ok" or result.data is None:
        return WidgetDataOut(status=result.status, error=result.error)
    return redact(type_key, result.data, show_details)


def redact_settings(
    type_key: str, settings: Mapping[str, Any], show_details: bool
) -> dict[str, Any]:
    """The subset of a widget's settings the wallboard needs to render it.

    Never the ticket source (a filter may name a customer), report key, parameters or
    free text. ``show_details`` does not widen it: details cover data, not configuration.
    """
    _ = show_details
    allowed = KIOSK_SETTINGS_WHITELIST.get(type_key, frozenset())
    return {k: v for k, v in settings.items() if k in allowed}


def kiosk_title(title_override: str | None, show_details: bool) -> str | None:
    """A custom widget title may name a customer: only with details."""
    return title_override if show_details else None


# --------------------------------------------------------------------------- store


def _is_active(row: TiqoraDashboardKioskToken, now: Any) -> bool:
    return row.revoked_at is None and row.expires_at > now


async def _row(session: AsyncSession, link_id: int) -> TiqoraDashboardKioskToken:
    res = await session.execute(
        select(TiqoraDashboardKioskToken)
        .where(TiqoraDashboardKioskToken.id == link_id)
        .execution_options(populate_existing=True)
    )
    row = res.scalar_one_or_none()
    if row is None:
        raise store.DashboardNotFound(link_id)
    return row


async def _out(session: AsyncSession, rows: list[TiqoraDashboardKioskToken]) -> list[KioskLinkOut]:
    if not rows:
        return []
    now = utc_now()
    ids = [r.id for r in rows]
    items = (
        await session.execute(
            select(TiqoraDashboardKioskItem)
            .where(TiqoraDashboardKioskItem.token_id.in_(ids))
            .order_by(TiqoraDashboardKioskItem.token_id, TiqoraDashboardKioskItem.position)
        )
    ).scalars()
    by_link: dict[int, list[int]] = {}
    for item in items:
        by_link.setdefault(int(item.token_id), []).append(int(item.dashboard_id))
    dash_ids = sorted({d for ds in by_link.values() for d in ds})
    names: dict[int, str] = {}
    if dash_ids:  # integer IN-list over tiqora ids
        names = {
            int(i): str(n)
            for i, n in (
                await session.execute(
                    select(TiqoraDashboard.id, TiqoraDashboard.name).where(
                        TiqoraDashboard.id.in_(dash_ids)
                    )
                )
            ).all()
        }
    creators = sorted({int(r.created_by) for r in rows})
    creator_names: dict[int, str] = {}
    for uid, first, last, login in (
        await session.execute(
            select(Users.id, Users.first_name, Users.last_name, Users.login).where(
                Users.id.in_(creators)
            )
        )
    ).all():
        creator_names[int(uid)] = f"{first or ''} {last or ''}".strip() or str(login)
    engine = PermissionEngine(session)
    is_admin = {uid: await engine.is_admin(uid) for uid in creators}
    out: list[KioskLinkOut] = []
    for r in rows:
        refs = [
            KioskDashboardRef(
                id=d,
                name=names.get(d, ""),
                visible=await store.is_visible(
                    session, d, int(r.created_by), admin=is_admin[int(r.created_by)]
                ),
            )
            for d in by_link.get(int(r.id), [])
        ]
        out.append(
            KioskLinkOut(
                id=int(r.id),
                name=r.name,
                dashboards=refs,
                rotate_seconds=int(r.rotate_seconds),
                show_details=bool(r.show_details),
                token_hint=r.token_hint,
                created_by=int(r.created_by),
                created_by_name=creator_names.get(int(r.created_by)),
                created_at=r.created_at,
                expires_at=r.expires_at,
                revoked_at=r.revoked_at,
                last_used_at=r.last_used_at,
                active=_is_active(r, now),
            )
        )
    return out


async def create_link(
    session: AsyncSession, admin_id: int, body: KioskLinkIn
) -> tuple[KioskLinkOut, str]:
    """Create a link; every dashboard must be visible to the creating admin.

    Returns the link and the secret, which is shown exactly once.
    """
    for dashboard_id in body.dashboard_ids:
        await store.get_dashboard(session, dashboard_id, admin_id, admin=True)
    token, digest, hint = new_token()
    now = utc_now()
    row = TiqoraDashboardKioskToken(
        token_hash=digest,
        token_hint=hint,
        name=body.name,
        created_by=admin_id,
        rotate_seconds=body.rotate_seconds,
        show_details=body.show_details,
        expires_at=now + timedelta(days=body.expires_in_days),
        created_at=now,
    )
    session.add(row)
    await session.flush()
    for position, dashboard_id in enumerate(body.dashboard_ids):
        session.add(
            TiqoraDashboardKioskItem(token_id=row.id, dashboard_id=dashboard_id, position=position)
        )
    await session.flush()
    logger.info("dashboard_kiosk_link_created", link_id=row.id, created_by=admin_id)
    return (await _out(session, [row]))[0], token


async def list_links(session: AsyncSession) -> list[KioskLinkOut]:
    """All links, active ones first, newest first within each group."""
    rows = list(
        (
            await session.execute(
                select(TiqoraDashboardKioskToken).order_by(TiqoraDashboardKioskToken.id.desc())
            )
        )
        .scalars()
        .all()
    )
    now = utc_now()
    rows.sort(key=lambda r: not _is_active(r, now))  # stable: id desc inside each group
    return await _out(session, rows)


async def revoke_link(session: AsyncSession, link_id: int) -> KioskLinkOut:
    """Set ``revoked_at`` once; revoking a revoked link changes nothing."""
    row = await _row(session, link_id)
    if row.revoked_at is None:
        await session.execute(
            update(TiqoraDashboardKioskToken)
            .where(
                TiqoraDashboardKioskToken.id == link_id,
                TiqoraDashboardKioskToken.revoked_at.is_(None),
            )
            .values(revoked_at=utc_now())
        )
        await session.flush()
        row = await _row(session, link_id)
        logger.info("dashboard_kiosk_link_revoked", link_id=link_id)
    return (await _out(session, [row]))[0]


async def delete_link(session: AsyncSession, link_id: int) -> None:
    """Delete a revoked or expired link with its dashboard rows."""
    row = await _row(session, link_id)
    if _is_active(row, utc_now()):
        raise store.DashboardInvalid(MSG_REVOKE_FIRST, "link")
    await session.execute(
        delete(TiqoraDashboardKioskItem).where(TiqoraDashboardKioskItem.token_id == link_id)
    )
    await session.execute(
        delete(TiqoraDashboardKioskToken).where(TiqoraDashboardKioskToken.id == link_id)
    )
    await session.flush()
    logger.info("dashboard_kiosk_link_deleted", link_id=link_id)
