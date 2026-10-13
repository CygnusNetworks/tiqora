"""Schedule store: rights, recipients, next run and the read models (plan D8, D11).

Rights (D8): own schedules need ``rights.create``; admins see and edit all of them, but a
schedule keeps running with its **owner's** permissions, so every rule below (share right,
share groups, report keys) is evaluated against the owner, also when an admin saves.
Recipients other than exactly the owner need the share right. That right means "any agent
or role" (not only the roles the owner belongs to); groups are restricted to the owner's
share groups. Nothing here JOINs ``tiqora_*`` with Znuny tables: keys are loaded first and
the other table is read with an integer IN-list.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal, cast

from pydantic import ValidationError
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.legacy.user import PermissionGroups, Roles, RoleUser, Users
from tiqora.db.tiqora.models import (
    TiqoraReport,
    TiqoraReportSchedule,
    TiqoraReportScheduleItem,
    TiqoraReportScheduleRecipient,
)
from tiqora.domain.timezones import resolve_user_time_zone, valid_time_zone
from tiqora.domain.user_preferences import bulk_get_preferences
from tiqora.extensions.core import get_registry
from tiqora.permissions.engine import PermissionEngine
from tiqora.permissions.sharing import ShareGroupError, check_share_groups
from tiqora.reporting.catalogue import active_builtin, is_catalogue_key
from tiqora.reporting.definition import DefinitionError
from tiqora.reporting.labels import resolve_labels
from tiqora.reporting.recurrence import Recurrence, describe, next_occurrence
from tiqora.reporting.rights import ReportRights, report_rights
from tiqora.reporting.saved import (
    SAVED_KEY_RE,
    ReportGone,
    ReportNotFound,
    resolve_report_ref,
)
from tiqora.reporting.schedule_schemas import (
    ReportScheduleIn,
    ReportScheduleListItemOut,
    ReportScheduleOut,
    ReportScheduleUpdateIn,
    ScheduleItemIn,
    ScheduleItemOut,
    ScheduleItemOverrides,
    ScheduleItemSummaryOut,
    ScheduleRecipientOut,
    ScheduleRecipientsIn,
    normalise_address,
)
from tiqora.reporting.schemas import ReportOwnerOut
from tiqora.reporting.settings import ReportingSettings
from tiqora.reporting.texts import load_texts
from tiqora.znuny.sysconfig import SysConfig

MAX_SCHEDULES_PER_USER: Final = 50
FAILURES_BEFORE_PAUSE: Final = 3
#: page cap of the admin list (``all=true``)
MAX_LISTED_SCHEDULES: Final = 200

NOT_FOUND_MESSAGE: Final = "Der Zeitplan wurde nicht gefunden."
CREATE_RIGHT_MESSAGE: Final = "Für Zeitpläne fehlt die Berechtigung."
SHARE_RIGHT_MESSAGE: Final = (
    "Für das Versenden an andere fehlt die Berechtigung (Gruppe „stats“, Schreibrecht)."
)
EXTERNAL_OFF_MESSAGE: Final = (
    "Externe Empfänger sind nicht freigeschaltet. Bitte wenden Sie sich an einen Administrator."
)
EXTERNAL_OFF_PROBLEM: Final = "Externe Empfänger sind ausgeschaltet."
NO_EMAIL_PROBLEM: Final = "Keine E-Mail-Adresse hinterlegt."
INVALID_PROBLEM: Final = "Ungültig."
LIMIT_MESSAGE: Final = f"Es sind höchstens {MAX_SCHEDULES_PER_USER} Zeitpläne pro Person möglich."
NO_RECIPIENT_MESSAGE: Final = "Bitte mindestens einen Empfänger angeben."

_STATUSES: Final = ("succeeded", "partial", "failed", "skipped")


class ScheduleNotFound(LookupError):
    """Unknown id, or neither owned by the viewer nor viewed by an admin."""


class ScheduleForbidden(PermissionError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ScheduleConflict(Exception):
    def __init__(self, current_version: int) -> None:
        super().__init__(current_version)
        self.current_version = current_version


class ScheduleInvalid(ValueError):
    def __init__(self, message: str, *, path: str) -> None:
        super().__init__(message)
        self.message = message
        self.path = path


@dataclass(frozen=True, slots=True)
class ScheduleRecipients:
    user_ids: tuple[int, ...]
    group_ids: tuple[int, ...]
    role_ids: tuple[int, ...]
    emails: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ScheduleItem:
    report_key: str
    overrides: ScheduleItemOverrides


@dataclass(frozen=True, slots=True)
class ReportSchedule:
    id: int
    owner_user_id: int
    name: str
    format: str
    recurrence: Recurrence | None
    recurrence_error: str | None
    time_zone: str
    language: str
    subject: str
    body: str
    only_if_nonempty: bool
    active: bool
    paused_reason: str | None
    next_run_at: datetime | None
    last_run_at: datetime | None
    last_status: str | None
    failure_count: int
    items: tuple[ScheduleItem, ...]
    recipients: ScheduleRecipients
    version: int
    created_at: datetime
    changed_at: datetime


@dataclass(frozen=True, slots=True)
class ResolvedRecipients:
    #: deduplicated case-insensitively, sorted, capped at ``schedule_max_recipients``
    addresses: tuple[str, ...]
    #: German lines WITHOUT addresses
    problems: tuple[str, ...]


# ------------------------------------------------------------------ loading


def _parse_recurrence(raw: str) -> tuple[Recurrence | None, str | None]:
    try:
        return Recurrence.model_validate_json(raw), None
    except ValidationError:
        return None, "Die Wiederholung kann nicht mehr gelesen werden."


def _parse_overrides(raw: str | None) -> ScheduleItemOverrides:
    if not raw:
        return ScheduleItemOverrides()
    try:
        return ScheduleItemOverrides.model_validate_json(raw)
    except ValidationError:
        return ScheduleItemOverrides()


def _dump_overrides(overrides: ScheduleItemOverrides) -> str | None:
    dumped = overrides.model_dump(mode="json", exclude_none=True)
    if not dumped.get("parameters"):
        dumped.pop("parameters", None)
    return json.dumps(dumped, sort_keys=True) if dumped else None


def _recipients_of(rows: Iterable[TiqoraReportScheduleRecipient]) -> ScheduleRecipients:
    users: list[int] = []
    groups: list[int] = []
    roles: list[int] = []
    emails: list[str] = []
    for row in sorted(rows, key=lambda r: int(r.id)):
        if row.kind == "user" and row.ref_id is not None:
            users.append(int(row.ref_id))
        elif row.kind == "group" and row.ref_id is not None:
            groups.append(int(row.ref_id))
        elif row.kind == "role" and row.ref_id is not None:
            roles.append(int(row.ref_id))
        elif row.kind == "email" and row.email:
            emails.append(row.email)
    return ScheduleRecipients(tuple(users), tuple(groups), tuple(roles), tuple(emails))


def _to_schedule(
    row: TiqoraReportSchedule,
    items: Sequence[TiqoraReportScheduleItem],
    recipients: Sequence[TiqoraReportScheduleRecipient],
) -> ReportSchedule:
    recurrence, error = _parse_recurrence(row.recurrence)
    return ReportSchedule(
        id=int(row.id),
        owner_user_id=int(row.owner_user_id),
        name=row.name,
        format=row.format,
        recurrence=recurrence,
        recurrence_error=error,
        time_zone=row.time_zone,
        language=row.language,
        subject=row.subject,
        body=row.body,
        only_if_nonempty=bool(row.only_if_nonempty),
        active=bool(row.active),
        paused_reason=row.paused_reason,
        next_run_at=row.next_run_at,
        last_run_at=row.last_run_at,
        last_status=row.last_status,
        failure_count=int(row.failure_count),
        items=tuple(
            ScheduleItem(i.report_key, _parse_overrides(i.overrides))
            for i in sorted(items, key=lambda i: int(i.position))
        ),
        recipients=_recipients_of(recipients),
        version=int(row.version),
        created_at=row.created_at,
        changed_at=row.changed_at,
    )


async def _hydrate(
    session: AsyncSession, rows: Sequence[TiqoraReportSchedule]
) -> list[ReportSchedule]:
    ids = [int(r.id) for r in rows]
    if not ids:
        return []
    item_rows = (
        (
            await session.execute(
                select(TiqoraReportScheduleItem)
                .where(TiqoraReportScheduleItem.schedule_id.in_(ids))
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    recipient_rows = (
        (
            await session.execute(
                select(TiqoraReportScheduleRecipient)
                .where(TiqoraReportScheduleRecipient.schedule_id.in_(ids))
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    items: dict[int, list[TiqoraReportScheduleItem]] = {}
    for item in item_rows:
        items.setdefault(int(item.schedule_id), []).append(item)
    recipients: dict[int, list[TiqoraReportScheduleRecipient]] = {}
    for recipient in recipient_rows:
        recipients.setdefault(int(recipient.schedule_id), []).append(recipient)
    return [_to_schedule(r, items.get(int(r.id), []), recipients.get(int(r.id), [])) for r in rows]


async def load_schedule(session: AsyncSession, schedule_id: int) -> ReportSchedule | None:
    """Worker use: no rights check, always fresh from the database."""
    row = (
        await session.execute(
            select(TiqoraReportSchedule)
            .where(TiqoraReportSchedule.id == schedule_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    return (await _hydrate(session, [row]))[0]


async def list_schedules(
    session: AsyncSession, user_id: int, *, admin: bool, include_all: bool = False
) -> list[ReportSchedule]:
    stmt = (
        select(TiqoraReportSchedule)
        .order_by(TiqoraReportSchedule.name, TiqoraReportSchedule.id)
        .execution_options(populate_existing=True)
    )
    if admin and include_all:
        stmt = stmt.limit(MAX_LISTED_SCHEDULES)
    else:
        stmt = stmt.where(TiqoraReportSchedule.owner_user_id == user_id)
    rows = (await session.execute(stmt)).scalars().all()
    return await _hydrate(session, rows)


async def get_schedule(
    session: AsyncSession, schedule_id: int, user_id: int, *, admin: bool
) -> ReportSchedule:
    """Owner or admin; anything else is :class:`ScheduleNotFound` (no existence leak)."""
    schedule = await load_schedule(session, schedule_id)
    if schedule is None or (schedule.owner_user_id != user_id and not admin):
        raise ScheduleNotFound(str(schedule_id))
    return schedule


# ------------------------------------------------------------------ validation


async def check_items(
    session: AsyncSession, owner_id: int, items: Sequence[ScheduleItemIn]
) -> None:
    """Every report key must resolve for the owner, override parameters must exist."""
    admin = await PermissionEngine(session).is_admin(owner_id)
    for index, item in enumerate(items):
        path = f"items.{index}.report_key"
        try:
            resolved = await resolve_report_ref(session, item.report_key, owner_id, admin=admin)
        except ReportNotFound:
            raise ScheduleInvalid("Der Bericht ist nicht verfügbar.", path=path) from None
        except ReportGone as exc:
            raise ScheduleInvalid(f"Der Bericht „{exc.name}“ wurde gelöscht.", path=path) from None
        except DefinitionError as exc:
            raise ScheduleInvalid(exc.message, path=path) from None
        known = {p.key for p in resolved.definition.parameters}
        for key in item.overrides.parameters:
            if key not in known:
                raise ScheduleInvalid(
                    f"Der Bericht hat keinen Parameter „{key}“.",
                    path=f"items.{index}.overrides.parameters",
                )


async def check_recipients(
    session: AsyncSession,
    owner_id: int,
    owner_rights: ReportRights,
    recipients: ScheduleRecipientsIn | ScheduleRecipients,
    reporting: ReportingSettings,
) -> ScheduleRecipients:
    """D8. Accepts the request model and the stored dataclass (the run-time re-check)."""
    users = tuple(dict.fromkeys(recipients.user_ids))
    groups = tuple(dict.fromkeys(recipients.group_ids))
    roles = tuple(dict.fromkeys(recipients.role_ids))
    emails = tuple(dict.fromkeys(e.strip().lower() for e in recipients.emails))
    if not (users or groups or roles or emails):
        raise ScheduleInvalid(NO_RECIPIENT_MESSAGE, path="recipients")
    others = bool(groups or roles or emails or any(u != owner_id for u in users))
    if others and not owner_rights.share:
        raise ScheduleForbidden(SHARE_RIGHT_MESSAGE)
    if emails:
        if not reporting.schedule_external_recipients:
            raise ScheduleInvalid(EXTERNAL_OFF_MESSAGE, path="recipients.emails")
        for address in emails:
            if normalise_address(address) is None:
                raise ScheduleInvalid(
                    f"Keine gültige E-Mail-Adresse: {address[:80]}", path="recipients.emails"
                )
    if groups:
        try:
            groups = await check_share_groups(session, owner_id, groups)
        except ShareGroupError as exc:
            raise ScheduleInvalid(exc.message, path="recipients.group_ids") from None
    if roles:
        valid_roles = set(
            (
                await session.execute(
                    select(Roles.id).where(Roles.id.in_(roles), Roles.valid_id == 1)
                )
            )
            .scalars()
            .all()
        )
        for role_id in roles:
            if role_id not in valid_roles:
                raise ScheduleInvalid(
                    f"Die Rolle {role_id} ist nicht verfügbar.", path="recipients.role_ids"
                )
    if users:
        valid_users = set(
            (
                await session.execute(
                    select(Users.id).where(Users.id.in_(users), Users.valid_id == 1)
                )
            )
            .scalars()
            .all()
        )
        for user_id in users:
            if user_id not in valid_users:
                raise ScheduleInvalid(
                    f"Der Agent {user_id} ist nicht verfügbar.", path="recipients.user_ids"
                )
    return ScheduleRecipients(users, groups, roles, emails)


async def _resolve_zone(session: AsyncSession, owner_id: int, requested: str | None) -> str:
    if requested is None or not requested.strip():
        return await resolve_user_time_zone(session, SysConfig(session), owner_id)
    zone = valid_time_zone(requested)
    if zone is None:
        raise ScheduleInvalid(f"Unbekannte Zeitzone: {requested.strip()[:64]}", path="time_zone")
    return zone


def _next_run(recurrence: Recurrence, zone: str, active: bool, now: datetime) -> datetime | None:
    return next_occurrence(recurrence, zone, now) if active else None


async def _replace_children(
    session: AsyncSession,
    schedule_id: int,
    items: Sequence[ScheduleItemIn],
    recipients: ScheduleRecipients,
) -> None:
    await session.execute(
        delete(TiqoraReportScheduleItem).where(TiqoraReportScheduleItem.schedule_id == schedule_id)
    )
    await session.execute(
        delete(TiqoraReportScheduleRecipient).where(
            TiqoraReportScheduleRecipient.schedule_id == schedule_id
        )
    )
    for position, item in enumerate(items):
        session.add(
            TiqoraReportScheduleItem(
                schedule_id=schedule_id,
                position=position,
                report_key=item.report_key,
                overrides=_dump_overrides(item.overrides),
            )
        )
    for kind, ids in (
        ("user", recipients.user_ids),
        ("group", recipients.group_ids),
        ("role", recipients.role_ids),
    ):
        for ref in ids:
            session.add(
                TiqoraReportScheduleRecipient(schedule_id=schedule_id, kind=kind, ref_id=ref)
            )
    for address in recipients.emails:
        session.add(
            TiqoraReportScheduleRecipient(schedule_id=schedule_id, kind="email", email=address)
        )
    await session.flush()


def _clean_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned:
        raise ScheduleInvalid("Bitte einen Namen angeben.", path="name")
    return cleaned


# ------------------------------------------------------------------ writes


async def create_schedule(
    session: AsyncSession,
    user_id: int,
    rights: ReportRights,
    body: ReportScheduleIn,
    *,
    reporting: ReportingSettings,
    now: datetime,
) -> ReportSchedule:
    if not rights.create:
        raise ScheduleForbidden(CREATE_RIGHT_MESSAGE)
    name = _clean_name(body.name)
    owned = (
        await session.execute(
            select(func.count())
            .select_from(TiqoraReportSchedule)
            .where(TiqoraReportSchedule.owner_user_id == user_id)
        )
    ).scalar_one()
    if int(owned) >= MAX_SCHEDULES_PER_USER:
        raise ScheduleInvalid(LIMIT_MESSAGE, path="name")
    zone = await _resolve_zone(session, user_id, body.time_zone)
    await check_items(session, user_id, body.items)
    recipients = await check_recipients(session, user_id, rights, body.recipients, reporting)
    row = TiqoraReportSchedule(
        owner_user_id=user_id,
        name=name,
        format=body.format,
        recurrence=body.recurrence.model_dump_json(),
        time_zone=zone,
        language=body.language,
        subject=body.subject,
        body=body.body,
        only_if_nonempty=body.only_if_nonempty,
        active=body.active,
        paused_reason=None,
        next_run_at=_next_run(body.recurrence, zone, body.active, now),
        last_run_at=None,
        last_status=None,
        failure_count=0,
        version=1,
        created_at=now,
        changed_at=now,
    )
    session.add(row)
    await session.flush()
    await _replace_children(session, int(row.id), body.items, recipients)
    loaded = await load_schedule(session, int(row.id))
    assert loaded is not None
    return loaded


def _same_children(current: ReportSchedule, body: ReportScheduleUpdateIn) -> bool:
    """The body keeps the stored reports (with overrides) and recipients unchanged."""
    items = [(i.report_key, i.overrides.model_dump()) for i in current.items]
    if items != [(i.report_key, i.overrides.model_dump()) for i in body.items]:
        return False
    stored, sent = current.recipients, body.recipients
    return (
        set(stored.user_ids) == set(sent.user_ids)
        and set(stored.group_ids) == set(sent.group_ids)
        and set(stored.role_ids) == set(sent.role_ids)
        and {e.lower() for e in stored.emails} == {e.strip().lower() for e in sent.emails}
    )


async def update_schedule(
    session: AsyncSession,
    schedule_id: int,
    user_id: int,
    rights: ReportRights,
    body: ReportScheduleUpdateIn,
    *,
    reporting: ReportingSettings,
    now: datetime,
) -> ReportSchedule:
    """Owner (with ``rights.create``) or admin. Rules are evaluated against the OWNER."""
    current = await get_schedule(session, schedule_id, user_id, admin=rights.admin)
    owner_id = current.owner_user_id
    if owner_id == user_id and not rights.create:
        raise ScheduleForbidden(CREATE_RIGHT_MESSAGE)
    if body.version != current.version:
        raise ScheduleConflict(current.version)
    owner_rights = (
        rights if owner_id == user_id else await report_rights(session, owner_id, reporting)
    )
    name = _clean_name(body.name)
    zone = await _resolve_zone(session, owner_id, body.time_zone)
    # Pausing must work even when a stored report or recipient became invalid meanwhile
    # (externals switched off, share right lost): an inactive schedule with unchanged reports
    # and recipients sends nothing, so they are not checked again (the run re-checks anyway).
    pausing_only = not body.active and _same_children(current, body)
    if pausing_only:
        recipients = current.recipients
    else:
        await check_items(session, owner_id, body.items)
        recipients = await check_recipients(
            session, owner_id, owner_rights, body.recipients, reporting
        )
    schedule_changed = (
        current.recurrence != body.recurrence
        or current.time_zone != zone
        or current.active != body.active
        or current.next_run_at is None
    )
    next_run = (
        _next_run(body.recurrence, zone, body.active, now)
        if schedule_changed
        else current.next_run_at
    )
    values: dict[str, object] = {
        "name": name,
        "format": body.format,
        "recurrence": body.recurrence.model_dump_json(),
        "time_zone": zone,
        "language": body.language,
        "subject": body.subject,
        "body": body.body,
        "only_if_nonempty": body.only_if_nonempty,
        "active": body.active,
        "next_run_at": next_run,
        "version": TiqoraReportSchedule.version + 1,
        "changed_at": now,
    }
    if body.active:
        # resumed: the reason of an auto-pause is history now
        values["failure_count"] = 0
        values["paused_reason"] = None
    result = await session.execute(
        update(TiqoraReportSchedule)
        .where(
            TiqoraReportSchedule.id == schedule_id,
            TiqoraReportSchedule.version == body.version,
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 0:  # type: ignore[attr-defined]  # two concurrent saves
        fresh = await load_schedule(session, schedule_id)
        if fresh is None:
            raise ScheduleNotFound(str(schedule_id))
        raise ScheduleConflict(fresh.version)
    await _replace_children(session, schedule_id, body.items, recipients)
    updated = await load_schedule(session, schedule_id)
    assert updated is not None
    return updated


async def delete_schedule(
    session: AsyncSession, schedule_id: int, user_id: int, *, admin: bool
) -> None:
    """Hard delete (children cascade); finished runs stay until their retention ends."""
    await get_schedule(session, schedule_id, user_id, admin=admin)
    await session.execute(
        delete(TiqoraReportScheduleItem).where(TiqoraReportScheduleItem.schedule_id == schedule_id)
    )
    await session.execute(
        delete(TiqoraReportScheduleRecipient).where(
            TiqoraReportScheduleRecipient.schedule_id == schedule_id
        )
    )
    await session.execute(
        delete(TiqoraReportSchedule).where(TiqoraReportSchedule.id == schedule_id)
    )
    await session.flush()


# ------------------------------------------------------------------ recipients


def _person(first: str | None, last: str | None, user_id: int) -> str:
    """First + last name, else ``Agent #<id>``; never the login (it is often an address)."""
    name = " ".join(part.strip() for part in (first, last) if part and part.strip())
    return name or f"Agent #{user_id}"


#: public name for the delivery job (texts and mails name people the same way)
person_label = _person


async def _users(session: AsyncSession, ids: Sequence[int]) -> dict[int, tuple[str, bool]]:
    """``{id: (label, valid)}`` for the users that exist."""
    if not ids:
        return {}
    rows = await session.execute(
        select(Users.id, Users.first_name, Users.last_name, Users.valid_id).where(
            Users.id.in_(list(ids))
        )
    )
    return {int(i): (_person(f, ln, int(i)), v == 1) for i, f, ln, v in rows.all()}


async def _group_names(session: AsyncSession, ids: Sequence[int]) -> dict[int, tuple[str, bool]]:
    if not ids:
        return {}
    rows = await session.execute(
        select(PermissionGroups.id, PermissionGroups.name, PermissionGroups.valid_id).where(
            PermissionGroups.id.in_(list(ids))
        )
    )
    return {int(i): (str(n), v == 1) for i, n, v in rows.all()}


async def _role_names(session: AsyncSession, ids: Sequence[int]) -> dict[int, tuple[str, bool]]:
    if not ids:
        return {}
    rows = await session.execute(
        select(Roles.id, Roles.name, Roles.valid_id).where(Roles.id.in_(list(ids)))
    )
    return {int(i): (str(n), v == 1) for i, n, v in rows.all()}


async def _role_members(session: AsyncSession, role_ids: Sequence[int]) -> set[int]:
    if not role_ids:
        return set()
    rows = await session.execute(select(RoleUser.user_id).where(RoleUser.role_id.in_(role_ids)))
    return {int(u) for u in rows.scalars().all()}


def _usable_address(raw: str | None) -> str | None:
    return normalise_address(raw) if raw else None


async def resolve_recipients(
    session: AsyncSession,
    schedule: ReportSchedule,
    reporting: ReportingSettings,
    *,
    owner_only: bool,
) -> ResolvedRecipients:
    """The addresses a delivery goes to, plus German problem lines without any address."""
    problems: list[str] = []
    direct: list[int]
    expanded: set[int] = set()
    emails: list[str] = []
    if owner_only:
        direct = [schedule.owner_user_id]
    else:
        rec = schedule.recipients
        direct = list(rec.user_ids)
        engine = PermissionEngine(session)
        group_info = await _group_names(session, rec.group_ids)
        for group_id in rec.group_ids:
            label, valid = group_info.get(group_id, (f"Gruppe #{group_id}", False))
            if not valid:
                problems.append(f"Gruppe „{label}“: ungültig")
                continue
            expanded |= await engine.users_for_group_permission(group_id, "ro")
        role_info = await _role_names(session, rec.role_ids)
        valid_roles = [r for r in rec.role_ids if role_info.get(r, ("", False))[1]]
        for role_id in rec.role_ids:
            if role_id not in valid_roles:
                label = role_info.get(role_id, (f"Rolle #{role_id}", False))[0]
                problems.append(f"Rolle „{label}“: ungültig")
        expanded |= await _role_members(session, valid_roles)
        if rec.emails:
            if reporting.schedule_external_recipients:
                emails = [e for e in rec.emails if normalise_address(e)]
            else:
                problems.append(EXTERNAL_OFF_PROBLEM)
    users = await _users(session, sorted({*direct, *expanded}))
    prefs = await bulk_get_preferences(session, sorted(users), "UserEmail")
    addresses: dict[str, str] = {}
    missing_expanded = 0
    for user_id in dict.fromkeys([*direct, *sorted(expanded)]):
        label, valid = users.get(user_id, (f"Agent #{user_id}", False))
        if not valid:
            if user_id in direct:
                problems.append(f"{label}: ungültig")
            continue
        address = _usable_address(prefs.get(user_id))
        if address is None:
            if user_id in direct:
                problems.append(f"{label}: keine E-Mail-Adresse")
            else:
                missing_expanded += 1
            continue
        addresses.setdefault(address, address)
    if missing_expanded:
        problems.append(f"{missing_expanded} Empfänger ohne E-Mail-Adresse")
    for address in emails:
        addresses.setdefault(address.lower(), address.lower())
    ordered = sorted(addresses)
    if len(ordered) > reporting.schedule_max_recipients:
        over = len(ordered) - reporting.schedule_max_recipients
        problems.append(f"{over} Empfänger über dem Limit")
        ordered = ordered[: reporting.schedule_max_recipients]
    return ResolvedRecipients(addresses=tuple(ordered), problems=tuple(problems))


async def recipient_views(
    session: AsyncSession, schedule: ReportSchedule, reporting: ReportingSettings
) -> list[ScheduleRecipientOut]:
    """One row per stored entry with its current problem (for the editor and the detail)."""
    rec = schedule.recipients
    users = await _users(session, rec.user_ids)
    prefs = await bulk_get_preferences(
        session, [u for u in rec.user_ids if u in users], "UserEmail"
    )
    groups = await _group_names(session, rec.group_ids)
    roles = await _role_names(session, rec.role_ids)
    out: list[ScheduleRecipientOut] = []
    for user_id in rec.user_ids:
        label, valid = users.get(user_id, (f"Agent #{user_id}", False))
        problem = None
        if not valid:
            problem = INVALID_PROBLEM
        elif _usable_address(prefs.get(user_id)) is None:
            problem = NO_EMAIL_PROBLEM
        out.append(
            ScheduleRecipientOut(kind="user", id=user_id, email=None, label=label, problem=problem)
        )
    for group_id in rec.group_ids:
        label, valid = groups.get(group_id, (f"Gruppe #{group_id}", False))
        out.append(
            ScheduleRecipientOut(
                kind="group",
                id=group_id,
                email=None,
                label=label,
                problem=None if valid else INVALID_PROBLEM,
            )
        )
    for role_id in rec.role_ids:
        label, valid = roles.get(role_id, (f"Rolle #{role_id}", False))
        out.append(
            ScheduleRecipientOut(
                kind="role",
                id=role_id,
                email=None,
                label=label,
                problem=None if valid else INVALID_PROBLEM,
            )
        )
    for address in rec.emails:
        out.append(
            ScheduleRecipientOut(
                kind="email",
                id=None,
                email=address,
                label=address,
                problem=None if reporting.schedule_external_recipients else EXTERNAL_OFF_PROBLEM,
            )
        )
    return out


# ------------------------------------------------------------------ read models


def can_edit_schedule(schedule: ReportSchedule, user_id: int, rights: ReportRights) -> bool:
    return (schedule.owner_user_id == user_id and rights.create) or rights.admin


def _recurrence_text(schedule: ReportSchedule) -> str:
    if schedule.recurrence is None:
        return schedule.recurrence_error or ""
    return describe(schedule.recurrence, load_texts(schedule.language))


def _status(value: str | None) -> Literal["succeeded", "partial", "failed", "skipped"] | None:
    return cast(
        "Literal['succeeded', 'partial', 'failed', 'skipped'] | None",
        value if value in _STATUSES else None,
    )


async def _item_problem(
    session: AsyncSession, owner_id: int, owner_admin: bool, item: ScheduleItem
) -> tuple[str | None, str | None, str | None]:
    """``(title, title_key, problem)`` of one item, resolved for the owner."""
    try:
        resolved = await resolve_report_ref(session, item.report_key, owner_id, admin=owner_admin)
    except ReportNotFound:
        return None, None, "Der Bericht ist nicht mehr verfügbar."
    except ReportGone as exc:
        return exc.name, None, f"Der Bericht „{exc.name}“ wurde gelöscht."
    except DefinitionError as exc:
        return None, None, exc.message
    if resolved.builtin is not None:
        return None, resolved.builtin.title_key, None
    assert resolved.saved is not None
    return resolved.saved.name, None, None


async def _owners(session: AsyncSession, schedules: Sequence[ReportSchedule]) -> dict[str, str]:
    ids = sorted({s.owner_user_id for s in schedules})
    return await resolve_labels(session, "user", ids) if ids else {}


async def schedule_out(
    session: AsyncSession,
    schedule: ReportSchedule,
    *,
    viewer_id: int,
    rights: ReportRights,
    reporting: ReportingSettings,
) -> ReportScheduleOut:
    """Detail model; item availability is judged for the OWNER, never the viewer."""
    owner_admin = await PermissionEngine(session).is_admin(schedule.owner_user_id)
    items: list[ScheduleItemOut] = []
    for item in schedule.items:
        title, title_key, problem = await _item_problem(
            session, schedule.owner_user_id, owner_admin, item
        )
        items.append(
            ScheduleItemOut(
                report_key=item.report_key,
                title=title,
                title_key=title_key,
                available=problem is None,
                problem=problem,
                overrides=item.overrides,
            )
        )
    owners = await _owners(session, [schedule])
    return ReportScheduleOut(
        id=schedule.id,
        name=schedule.name,
        owner=ReportOwnerOut(
            user_id=schedule.owner_user_id,
            name=owners.get(str(schedule.owner_user_id), str(schedule.owner_user_id)),
        ),
        is_owner=schedule.owner_user_id == viewer_id,
        can_edit=can_edit_schedule(schedule, viewer_id, rights),
        items=items,
        format=cast("Literal['pdf', 'xlsx', 'csv']", schedule.format),
        recurrence=schedule.recurrence,
        recurrence_text=_recurrence_text(schedule),
        time_zone=schedule.time_zone,
        language=cast("Literal['de', 'en']", schedule.language),
        subject=schedule.subject,
        body=schedule.body,
        only_if_nonempty=schedule.only_if_nonempty,
        active=schedule.active,
        paused_reason=schedule.paused_reason,
        next_run_at=schedule.next_run_at,
        last_run_at=schedule.last_run_at,
        last_status=_status(schedule.last_status),
        failure_count=schedule.failure_count,
        recipients=await recipient_views(session, schedule, reporting),
        version=schedule.version,
        changed_at=schedule.changed_at,
    )


async def schedule_list_out(
    session: AsyncSession,
    schedules: Sequence[ReportSchedule],
    *,
    viewer_id: int,
    rights: ReportRights,
) -> list[ReportScheduleListItemOut]:
    """Lightweight rows: names of saved reports from ONE IN-query, no rights checks per item."""
    rows = list(schedules)
    saved_ids = sorted(
        {int(i.report_key) for s in rows for i in s.items if SAVED_KEY_RE.fullmatch(i.report_key)}
    )
    names: dict[int, str] = {}
    if saved_ids:
        found = await session.execute(
            select(TiqoraReport.id, TiqoraReport.name).where(TiqoraReport.id.in_(saved_ids))
        )
        names = {int(i): str(n) for i, n in found.all()}
    registry = get_registry()
    owners = await _owners(session, rows)
    out: list[ReportScheduleListItemOut] = []
    for schedule in rows:
        summaries: list[ScheduleItemSummaryOut] = []
        for item in schedule.items:
            title: str | None = None
            title_key: str | None = None
            if is_catalogue_key(item.report_key):
                builtin = active_builtin(registry, item.report_key)
                title_key = builtin.title_key if builtin is not None else None
            elif SAVED_KEY_RE.fullmatch(item.report_key):
                title = names.get(int(item.report_key))
            summaries.append(
                ScheduleItemSummaryOut(report_key=item.report_key, title=title, title_key=title_key)
            )
        out.append(
            ReportScheduleListItemOut(
                id=schedule.id,
                name=schedule.name,
                owner=ReportOwnerOut(
                    user_id=schedule.owner_user_id,
                    name=owners.get(str(schedule.owner_user_id), str(schedule.owner_user_id)),
                ),
                is_owner=schedule.owner_user_id == viewer_id,
                can_edit=can_edit_schedule(schedule, viewer_id, rights),
                items=summaries,
                format=cast("Literal['pdf', 'xlsx', 'csv']", schedule.format),
                recurrence_text=_recurrence_text(schedule),
                time_zone=schedule.time_zone,
                active=schedule.active,
                paused_reason=schedule.paused_reason,
                next_run_at=schedule.next_run_at,
                last_run_at=schedule.last_run_at,
                last_status=_status(schedule.last_status),
                failure_count=schedule.failure_count,
                version=schedule.version,
            )
        )
    return out
