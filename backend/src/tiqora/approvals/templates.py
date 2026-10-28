"""Approval templates ("Freigabevorlagen"): named, reusable ``ApprovalDefinition``s.

Admins manage them; consumers add their own once with :func:`ensure_template` (never
overwritten afterwards, so admin edits survive upgrades). Service functions flush, never commit.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

import structlog
from pydantic import ValidationError
from sqlalchemy import bindparam, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.approvals.contract import (
    APPROVAL_SUBJECT_TYPES,
    APPROVER_RESOLVERS,
    ApprovalDefinition,
    ApprovalError,
    ApproverEntry,
)
from tiqora.approvals.models import TiqoraApprovalList, TiqoraApprovalTemplate
from tiqora.extensions.core import get_registry
from tiqora.jobs.service import utc_now

logger = structlog.get_logger(__name__)

_CHUNK = 500
_ORIGIN_MAX = 16


@dataclass(frozen=True, slots=True)
class TemplateRow:
    id: int
    key: str
    name: str
    description: str | None
    subject_types: tuple[str, ...]
    definition: ApprovalDefinition
    origin: str
    valid: bool
    #: naive UTC, whole seconds: the version an editor sends back (``template_changed``)
    change_time: datetime


@dataclass(slots=True)
class RefCheck:
    """Errors block saving; warnings (approvers that vanished) do not."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _row(entity: TiqoraApprovalTemplate) -> TemplateRow:
    return TemplateRow(
        id=entity.id,
        key=entity.template_key,
        name=entity.name,
        description=entity.description,
        subject_types=tuple(json.loads(entity.subject_types)),
        definition=ApprovalDefinition.model_validate(json.loads(entity.definition)),
        origin=entity.origin,
        valid=bool(entity.valid),
        change_time=_second(entity.change_time),
    )


def _second(value: datetime) -> datetime:
    """Whole seconds, naive UTC: MariaDB ``DATETIME`` keeps no fraction, PostgreSQL does."""
    if value.tzinfo is not None:
        value = value.astimezone(UTC).replace(tzinfo=None)
    return value.replace(microsecond=0)


def _dump_subjects(subject_types: Iterable[str]) -> str:
    ordered = sorted(set(subject_types))
    return json.dumps(ordered)


def _check_subject_types(subject_types: Iterable[str]) -> list[str]:
    registry = get_registry()
    ordered = sorted(set(subject_types))
    for key in ordered:
        if registry.get(APPROVAL_SUBJECT_TYPES, key) is None:
            raise ApprovalError("unknown_subject_type", 422, key)
    return ordered


def _parse_definition(definition: ApprovalDefinition | dict[str, object]) -> ApprovalDefinition:
    if isinstance(definition, ApprovalDefinition):
        return definition
    try:
        return ApprovalDefinition.model_validate(definition)
    except ValidationError as exc:
        raise ApprovalError("invalid_definition", 422, str(exc.error_count()) + " errors") from exc


def _int_ref(entry: ApproverEntry) -> int | None:
    ref = entry.ref
    return int(ref) if ref.isascii() and ref.isdigit() else None


async def _existing_ids(session: AsyncSession, sql: str, ids: set[int]) -> set[int]:
    found: set[int] = set()
    ordered = sorted(ids)
    stmt = text(sql).bindparams(bindparam("ids", expanding=True))
    for start in range(0, len(ordered), _CHUNK):
        rows = await session.execute(stmt, {"ids": ordered[start : start + _CHUNK]})
        found.update(int(r[0]) for r in rows)
    return found


async def validate_definition_refs(
    session: AsyncSession,
    definition: ApprovalDefinition,
    *,
    subject_types: Sequence[str] = (),
) -> RefCheck:
    """Checks what a definition points at.

    Errors: ``unknown_resolver:<key>``, ``invalid_ref:<kind>:<ref>``,
    ``customer_entry_not_allowed`` (a customer entry or customer resolver for a listed subject
    type without ``allow_customer_approvers``). Warnings: ``missing_user:<id>``,
    ``missing_group:<id>``, ``missing_role:<id>``, ``missing_list:<id>``,
    ``missing_customer:<login>``. ``subject_types`` empty = every type: the customer rule is then
    not checked (the type is only known at request time).
    """
    registry = get_registry()
    check = RefCheck()
    customers_allowed = True
    for key in subject_types:
        subject = registry.get(APPROVAL_SUBJECT_TYPES, key)
        if subject is not None and not subject.allow_customer_approvers:
            customers_allowed = False

    wanted: dict[str, set[int]] = {"user": set(), "group": set(), "role": set(), "list": set()}
    logins: set[str] = set()
    for step in definition.steps:
        for entry in (*step.approvers, *step.escalation):
            if entry.kind == "resolver":
                resolver = registry.get(APPROVER_RESOLVERS, entry.ref)
                if resolver is None:
                    check.errors.append(f"unknown_resolver:{entry.ref}")
                elif resolver.yields_customers and not customers_allowed:
                    check.errors.append("customer_entry_not_allowed")
            elif entry.kind == "customer_user":
                if not customers_allowed:
                    check.errors.append("customer_entry_not_allowed")
                logins.add(entry.ref)
            else:
                number = _int_ref(entry)
                if number is None:
                    check.errors.append(f"invalid_ref:{entry.kind}:{entry.ref}")
                else:
                    wanted[entry.kind].add(number)

    present = {
        "user": await _existing_ids(
            session, "SELECT id FROM users WHERE valid_id = 1 AND id IN :ids", wanted["user"]
        ),
        "group": await _existing_ids(
            session,
            "SELECT id FROM permission_groups WHERE valid_id = 1 AND id IN :ids",
            wanted["group"],
        ),
        "role": await _existing_ids(
            session, "SELECT id FROM roles WHERE valid_id = 1 AND id IN :ids", wanted["role"]
        ),
    }
    if wanted["list"]:
        rows = await session.execute(
            select(TiqoraApprovalList.id).where(
                TiqoraApprovalList.id.in_(sorted(wanted["list"])),
                TiqoraApprovalList.valid.is_(True),
            )
        )
        present["list"] = {int(r[0]) for r in rows}
    else:
        present["list"] = set()
    for kind in ("user", "group", "role", "list"):
        for number in sorted(wanted[kind] - present[kind]):
            check.warnings.append(f"missing_{kind}:{number}")
    if logins:
        stmt = text(
            "SELECT login FROM customer_user WHERE valid_id = 1 AND login IN :ids"
        ).bindparams(bindparam("ids", expanding=True))
        have = {str(r[0]) for r in await session.execute(stmt, {"ids": sorted(logins)})}
        check.warnings.extend(f"missing_customer:{login}" for login in sorted(logins - have))
    # deterministic, no duplicates
    check.errors[:] = sorted(set(check.errors))
    return check


async def _template_by_key(session: AsyncSession, key: str) -> TiqoraApprovalTemplate | None:
    return (
        await session.execute(
            select(TiqoraApprovalTemplate).where(TiqoraApprovalTemplate.template_key == key)
        )
    ).scalar_one_or_none()


def _clean_key(key: str) -> str:
    key = key.strip()
    if not key or len(key) > 100:
        raise ApprovalError("invalid_definition", 422, "template key must be 1-100 characters")
    return key


def _clean_name(name: str) -> str:
    name = name.strip()
    if not name or len(name) > 200:
        raise ApprovalError("invalid_definition", 422, "template name must be 1-200 characters")
    return name


async def get_template(session: AsyncSession, template_id: int) -> TemplateRow:
    entity = await session.get(TiqoraApprovalTemplate, template_id)
    if entity is None:
        raise ApprovalError("template_not_found", 404)
    return _row(entity)


async def get_template_by_key(session: AsyncSession, key: str) -> TemplateRow:
    entity = await _template_by_key(session, key)
    if entity is None:
        raise ApprovalError("template_not_found", 404)
    return _row(entity)


async def list_templates(
    session: AsyncSession, *, subject_type: str | None = None, include_invalid: bool = False
) -> list[TemplateRow]:
    """Templates by name; ``subject_type`` keeps those listing it plus the all-types ones.

    A stored row that no longer parses (hand-edited, older format) is left out and logged.
    """
    stmt = select(TiqoraApprovalTemplate).order_by(
        TiqoraApprovalTemplate.name, TiqoraApprovalTemplate.id
    )
    if not include_invalid:
        stmt = stmt.where(TiqoraApprovalTemplate.valid.is_(True))
    rows: list[TemplateRow] = []
    for entity in (await session.execute(stmt)).scalars():
        try:
            rows.append(_row(entity))
        except ValueError as exc:  # json / pydantic: one unreadable stored row must not hide all
            logger.warning(
                "approval_template_unreadable", template_id=entity.id, error=type(exc).__name__
            )
    if subject_type is None:
        return rows
    return [r for r in rows if not r.subject_types or subject_type in r.subject_types]


async def _insert(session: AsyncSession, entity: TiqoraApprovalTemplate) -> bool:
    """Inserts *entity* in a savepoint; False when the unique key was taken meanwhile."""
    try:
        async with session.begin_nested():
            session.add(entity)
    except IntegrityError:
        return False
    return True


async def create_template(
    session: AsyncSession,
    *,
    key: str,
    name: str,
    definition: ApprovalDefinition | dict[str, object],
    subject_types: Iterable[str] = (),
    description: str | None = None,
    actor_id: int,
) -> TemplateRow:
    key = _clean_key(key)
    name = _clean_name(name)
    types = _check_subject_types(subject_types)
    parsed = _parse_definition(definition)
    check = await validate_definition_refs(session, parsed, subject_types=types)
    if check.errors:
        raise ApprovalError("invalid_definition", 422, ",".join(check.errors))
    if await _template_by_key(session, key) is not None:
        raise ApprovalError("template_key_taken", 409)
    now = utc_now().replace(microsecond=0)
    entity = TiqoraApprovalTemplate(
        template_key=key,
        name=name,
        description=description,
        subject_types=_dump_subjects(types),
        definition=parsed.model_dump_json(),
        origin="admin",
        valid=True,
        create_time=now,
        create_by=actor_id,
        change_time=now,
        change_by=actor_id,
    )
    if not await _insert(session, entity):  # a concurrent insert took the key after the check
        raise ApprovalError("template_key_taken", 409)
    return _row(entity)


async def update_template(
    session: AsyncSession,
    template_id: int,
    *,
    actor_id: int,
    name: str | None = None,
    description: str | None = None,
    definition: ApprovalDefinition | dict[str, object] | None = None,
    clear_description: bool = False,
    subject_types: Iterable[str] | None = None,
    valid: bool | None = None,
    expected_change_time: datetime | None = None,
) -> TemplateRow:
    """Changes the given fields. The key is immutable (consumers address templates by it).

    ``description=None`` leaves the text alone; ``clear_description=True`` empties it (a PUT
    that replaces the whole record). With *expected_change_time* (the ``change_time`` the
    editor loaded) a template changed since - by another admin or a GDPR erasure - is refused
    with ``template_changed`` (409) instead of being overwritten.
    """
    entity = await session.get(TiqoraApprovalTemplate, template_id, with_for_update=True)
    if entity is None:
        raise ApprovalError("template_not_found", 404)
    current = _row(entity)
    if expected_change_time is not None and _second(expected_change_time) != current.change_time:
        raise ApprovalError("template_changed", 409)
    types = (
        current.subject_types
        if subject_types is None
        else tuple(_check_subject_types(subject_types))
    )
    parsed = current.definition if definition is None else _parse_definition(definition)
    if definition is not None or subject_types is not None:
        check = await validate_definition_refs(session, parsed, subject_types=types)
        if check.errors:
            raise ApprovalError("invalid_definition", 422, ",".join(check.errors))
    if name is not None:
        entity.name = _clean_name(name)
    if clear_description:
        entity.description = None
    elif description is not None:
        entity.description = description
    if valid is not None:
        entity.valid = valid
    entity.subject_types = _dump_subjects(types)
    entity.definition = parsed.model_dump_json()
    entity.change_time = utc_now().replace(microsecond=0)
    entity.change_by = actor_id
    await session.flush()
    return _row(entity)


async def delete_template(session: AsyncSession, template_id: int) -> None:
    """Running requests keep their snapshot; their ``template_id`` becomes NULL (FK SET NULL).

    An ORM delete: only this entity leaves the session (no ``expire_all`` on the caller's
    session, whose other loaded objects stay usable).
    """
    entity = await session.get(TiqoraApprovalTemplate, template_id)
    if entity is None:
        raise ApprovalError("template_not_found", 404)
    await session.delete(entity)
    await session.flush()


async def ensure_template(
    session: AsyncSession,
    *,
    key: str,
    name: str,
    definition: ApprovalDefinition,
    origin: str,
    subject_types: Iterable[str] = (),
    description: str | None = None,
) -> TemplateRow:
    """Inserts the template if its key is free; an existing row is returned untouched.

    Two workers seeding at once are fine: the loser's insert fails in a savepoint and it reads
    the winner's row instead (the caller's transaction goes on).
    """
    key = _clean_key(key)
    if not origin or len(origin) > _ORIGIN_MAX:
        raise ApprovalError("invalid_definition", 422, f"origin must be 1-{_ORIGIN_MAX} characters")
    existing = await _template_by_key(session, key)
    if existing is not None:
        return _row(existing)
    now = utc_now().replace(microsecond=0)
    entity = TiqoraApprovalTemplate(
        template_key=key,
        name=_clean_name(name),
        description=description,
        subject_types=_dump_subjects(subject_types),
        definition=definition.model_dump_json(),
        origin=origin,
        valid=True,
        create_time=now,
        create_by=1,
        change_time=now,
        change_by=1,
    )
    if not await _insert(session, entity):
        winner = await _template_by_key(session, key)
        if winner is None:  # the other insert was rolled back again: nothing to share
            raise ApprovalError("template_key_taken", 409)
        return _row(winner)
    return _row(entity)
