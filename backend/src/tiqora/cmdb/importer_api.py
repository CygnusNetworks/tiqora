"""Write API of the Tiqora CMDB for the Znuny ITSM import (C6, rulings R5/R9).

The importer never writes ``tiqora_cmdb_*`` / ``tiqora_itsm_link`` rows by any other path. Every
function works on the caller's session, flushes and never commits (the caller owns the
transaction). Imported history is taken as-is: no validation against the class definition, the
given times and authors are kept. Conversion of Znuny data shapes (``XMLData`` <-> attribute
JSON) is not part of this module; the import owns it. Link types are not created here either:
``import_link`` needs the type row to exist (``tiqora_itsm_link_type``, itsm1).

Typical bulk run: ``upsert_*`` and ``add_definition``, then per CI ``create_imported_ci`` and
``add_imported_version`` (or ``import_ci`` for all versions at once), links, attachments and
history, and finally ``finish_import`` (index, number counters, derived incident states).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.cmdb.attachments import (
    EVENT_ADD,
    sanitize_content_type,
    sanitize_filename,
)
from tiqora.cmdb.catalog import CatalogService
from tiqora.cmdb.classes import ClassData, ClassService
from tiqora.cmdb.definition import AttributeDef, dump_definition_json
from tiqora.cmdb.errors import CmdbNotFound
from tiqora.cmdb.index import rebuild_index
from tiqora.cmdb.links import LinkTypeService, _end
from tiqora.cmdb.numbering import bump_counter_for_number
from tiqora.cmdb.propagation import PropagationResult, recompute_from
from tiqora.cmdb.seed import DEPLOYMENT_STATE, INCIDENT_STATE
from tiqora.cmdb.service import CiService, system_access
from tiqora.cmdb.values import canonical_json
from tiqora.cmdb.znuny_definition import (
    DefinitionImportResult,
    DefinitionParseError,
    parse_znuny_definition,
)
from tiqora.db.tiqora.models import (
    TiqoraCmdbAttachment,
    TiqoraCmdbCatalog,
    TiqoraCmdbCi,
    TiqoraCmdbCiHistory,
    TiqoraCmdbCiVersion,
    TiqoraCmdbClass,
    TiqoraCmdbClassDefinition,
    TiqoraItsmLink,
)

#: states a new imported CI gets when the caller names none (F27)
DEFAULT_DEPLOYMENT_KEY = "production"
DEFAULT_INCIDENT_KEY = "operational"

_HISTORY_DETAIL_MAX = 1000
_ID_CHUNK = 1000


@dataclass(frozen=True, slots=True)
class ImportedVersion:
    """One CI version to import: states are catalog ids, ``attributes`` the D4 document."""

    definition_id: int
    name: str
    deployment_state_id: int
    incident_state_id: int
    attributes: dict[str, Any]
    created_by: int
    created_at: datetime
    change_note: str | None = None


# --- catalog, classes, definitions ------------------------------------------------------------


async def upsert_catalog_item(
    session: AsyncSession,
    *,
    catalog_class: str,
    item_key: str,
    name: str,
    functionality: str | None,
    valid: bool,
    position: int = 0,
) -> int:
    """Create the catalog item or bring an existing one (same class and key) up to date."""
    svc = CatalogService(session)
    for item in await svc.items(catalog_class, include_invalid=True):
        if item.item_key == item_key:
            if (item.name, item.functionality, item.valid, item.position) != (
                name,
                functionality,
                valid,
                position,
            ):
                await svc.update(
                    item.id,
                    name=name,
                    functionality=functionality,
                    valid=valid,
                    position=position,
                )
            return item.id
    created = await svc.create(
        catalog_class,
        item_key,
        name,
        functionality=functionality,
        position=position,
        valid=valid,
    )
    return created.id


async def upsert_class(
    session: AsyncSession,
    *,
    class_key: str,
    name: str,
    group_id: int,
    number_prefix: str,
    user_id: int,
    icon: str | None = None,
    portal_visible: bool = False,
) -> int:
    """Create the class or update name, group, prefix, icon and portal flag of an existing one
    (``valid`` and ``position`` of an existing class are kept; an unchanged class is not
    touched)."""
    svc = ClassService(session)
    found = (
        await session.execute(
            select(TiqoraCmdbClass.id).where(TiqoraCmdbClass.class_key == class_key)
        )
    ).scalar_one_or_none()
    if found is None:
        created = await svc.create(
            ClassData(
                class_key=class_key,
                name=name,
                group_id=group_id,
                icon=icon,
                number_prefix=number_prefix,
                portal_visible=portal_visible,
            ),
            user_id=user_id,
        )
        return created.id
    cur = await svc.get(int(found))
    if (cur.name, cur.group_id, cur.number_prefix, cur.icon, cur.portal_visible) != (
        name,
        group_id,
        number_prefix,
        icon,
        portal_visible,
    ):
        await svc.update(
            cur.id,
            ClassData(
                class_key=class_key,
                name=name,
                group_id=group_id,
                icon=icon,
                number_prefix=number_prefix,
                portal_visible=portal_visible,
                valid=cur.valid,
                position=cur.position,
            ),
            expected_lock_version=cur.lock_version,
            user_id=user_id,
        )
    return cur.id


async def add_definition(
    session: AsyncSession,
    *,
    class_id: int,
    attributes: list[AttributeDef],
    comment: str | None,
    created_by: int,
    created_at: datetime,
    make_current: bool = True,
    skip_if_equal: bool = True,
    extra_class_keys: frozenset[str] = frozenset(),
) -> int:
    """Append a definition version; returns its id.

    ``skip_if_equal`` (default) makes re-runs idempotent: when the newest version of the class
    has the same canonical JSON (``dump_definition_json``), nothing is added and that
    version's id is returned (it becomes the current one only if the class has none).
    """
    if skip_if_equal:
        latest = (
            await session.execute(
                select(TiqoraCmdbClassDefinition)
                .where(TiqoraCmdbClassDefinition.class_id == class_id)
                .order_by(TiqoraCmdbClassDefinition.version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if latest is not None and latest.attributes == dump_definition_json(attributes):
            if make_current:
                await session.execute(
                    update(TiqoraCmdbClass)
                    .where(
                        TiqoraCmdbClass.id == class_id,
                        TiqoraCmdbClass.current_definition_id.is_(None),
                    )
                    .values(current_definition_id=latest.id)
                    .execution_options(synchronize_session=False)
                )
            return int(latest.id)
    rec = await ClassService(session).add_definition(
        class_id,
        attributes,
        comment=comment,
        user_id=created_by,
        created_at=created_at,
        make_current=make_current,
        extra_class_keys=extra_class_keys,
    )
    return rec.id


# --- configuration items ----------------------------------------------------------------------


async def _default_state(session: AsyncSession, catalog_class: str, key: str) -> int:
    found = (
        await session.execute(
            select(TiqoraCmdbCatalog.id).where(
                TiqoraCmdbCatalog.catalog_class == catalog_class,
                TiqoraCmdbCatalog.item_key == key,
            )
        )
    ).scalar_one_or_none()
    if found is None:
        raise CmdbNotFound(f"catalog item {catalog_class}/{key}")
    return int(found)


async def _ci_id_by_number(session: AsyncSession, number: str) -> int | None:
    found = (
        await session.execute(select(TiqoraCmdbCi.id).where(TiqoraCmdbCi.number == number))
    ).scalar_one_or_none()
    return None if found is None else int(found)


def _history_row(
    ci_id: int, event: str, detail: str, version_id: int | None, user_id: int, at: datetime
) -> TiqoraCmdbCiHistory:
    return TiqoraCmdbCiHistory(
        ci_id=ci_id,
        event=event,
        detail=detail[:_HISTORY_DETAIL_MAX],
        version_id=version_id,
        created_by=user_id,
        created_at=at,
    )


async def create_imported_ci(
    session: AsyncSession,
    *,
    class_id: int,
    number: str,
    name: str,
    created_by: int,
    created_at: datetime,
    deployment_state_id: int | None = None,
    incident_state_id: int | None = None,
) -> int:
    """Create the CI header with the given number and creation data, without a version.

    Idempotent by ``number`` (an existing CI's id is returned untouched). A state left at
    ``None`` defaults to deployment ``production`` / incident ``operational``. Add the
    versions with :func:`add_imported_version`.
    """
    existing = await _ci_id_by_number(session, number)
    if existing is not None:
        return existing
    if await session.get(TiqoraCmdbClass, class_id) is None:
        raise CmdbNotFound(f"class {class_id}")
    dep = (
        deployment_state_id
        if deployment_state_id is not None
        else await _default_state(session, DEPLOYMENT_STATE, DEFAULT_DEPLOYMENT_KEY)
    )
    inc = (
        incident_state_id
        if incident_state_id is not None
        else await _default_state(session, INCIDENT_STATE, DEFAULT_INCIDENT_KEY)
    )
    row = TiqoraCmdbCi(
        number=number,
        class_id=class_id,
        name=name.strip()[:250] or number,
        deployment_state_id=dep,
        incident_state_id=inc,
        cur_incident_state_id=inc,
        current_version_id=None,
        version_count=0,
        lock_version=1,
        created_by=created_by,
        created_at=created_at,
        changed_by=created_by,
        changed_at=created_at,
    )
    session.add(row)
    await session.flush()
    session.add(_history_row(int(row.id), "ConfigItemCreate", number, None, created_by, created_at))
    await session.flush()
    return int(row.id)


async def latest_version_id(session: AsyncSession, ci_id: int) -> int | None:
    """The id of the CI's current version (``None`` for a CI without versions)."""
    row = (
        await session.execute(
            select(TiqoraCmdbCi.id, TiqoraCmdbCi.current_version_id).where(TiqoraCmdbCi.id == ci_id)
        )
    ).first()
    if row is None:
        raise CmdbNotFound(f"ci {ci_id}")
    return None if row[1] is None else int(row[1])


async def add_imported_version(
    session: AsyncSession,
    ci_id: int,
    version: ImportedVersion,
    *,
    write_index: bool = True,
    history: bool = True,
) -> int | None:
    """Append ``version`` as the newest version of the CI and return its id.

    ``None`` means the version equals the current one (name, states and attribute document)
    and nothing was written. The header takes name, states and change data from the version;
    the derived incident state starts at the version's own state until ``finish_import``
    recomputes it. ``write_index=False`` leaves the search index to ``finish_import`` (bulk
    runs), ``history=False`` skips the ``VersionCreate`` row (the history is imported
    separately). The values are stored as given, without validation.
    """
    ci = (
        await session.execute(
            select(TiqoraCmdbCi)
            .where(TiqoraCmdbCi.id == ci_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if ci is None:
        raise CmdbNotFound(f"ci {ci_id}")
    document = canonical_json(version.attributes)
    name = version.name.strip()[:250] or str(ci.number)
    if ci.current_version_id is not None:
        current = (
            await session.execute(
                select(TiqoraCmdbCiVersion).where(TiqoraCmdbCiVersion.id == ci.current_version_id)
            )
        ).scalar_one_or_none()
        if (
            current is not None
            and current.name == name
            and int(current.deployment_state_id) == version.deployment_state_id
            and int(current.incident_state_id) == version.incident_state_id
            and canonical_json(json.loads(current.attributes)) == document
        ):
            return None
    row = TiqoraCmdbCiVersion(
        ci_id=ci_id,
        definition_id=version.definition_id,
        name=name,
        deployment_state_id=version.deployment_state_id,
        incident_state_id=version.incident_state_id,
        attributes=document,
        change_note=version.change_note,
        created_by=version.created_by,
        created_at=version.created_at,
    )
    session.add(row)
    await session.flush()
    count = int(ci.version_count) + 1
    await session.execute(
        update(TiqoraCmdbCi)
        .where(TiqoraCmdbCi.id == ci_id)
        .values(
            name=name,
            deployment_state_id=version.deployment_state_id,
            incident_state_id=version.incident_state_id,
            cur_incident_state_id=version.incident_state_id,
            current_version_id=row.id,
            version_count=count,
            lock_version=int(ci.lock_version) + 1,
            changed_by=version.created_by,
            changed_at=version.created_at,
        )
        .execution_options(synchronize_session=False)
    )
    if history:
        session.add(
            _history_row(
                ci_id,
                "VersionCreate",
                str(count),
                int(row.id),
                version.created_by,
                version.created_at,
            )
        )
    await session.flush()
    if write_index:
        await rebuild_index(session, [ci_id])
    return int(row.id)


async def import_ci(
    session: AsyncSession,
    *,
    class_id: int,
    number: str,
    created_by: int,
    created_at: datetime,
    versions: Sequence[ImportedVersion],
) -> int:
    """Create the CI with all ``versions`` (ascending), or complete an existing one.

    Idempotent by ``number``: an existing CI only receives versions newer than its newest.
    The history is taken as-is (no validation against the definition); the index is written
    once, for the newest version.
    """
    ci_id = await _ci_id_by_number(session, number)
    ordered = sorted(versions, key=lambda v: v.created_at)
    if ci_id is None:
        if not ordered:
            raise ValueError("import_ci needs at least one version for a new CI")
        first = ordered[0]
        ci_id = await create_imported_ci(
            session,
            class_id=class_id,
            number=number,
            name=first.name,
            created_by=created_by,
            created_at=created_at,
            deployment_state_id=first.deployment_state_id,
            incident_state_id=first.incident_state_id,
        )
        newest: datetime | None = None
    else:
        newest = (
            await session.execute(
                select(TiqoraCmdbCiVersion.created_at)
                .where(TiqoraCmdbCiVersion.ci_id == ci_id)
                .order_by(TiqoraCmdbCiVersion.created_at.desc(), TiqoraCmdbCiVersion.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    added = False
    for version in ordered:
        if newest is not None and version.created_at <= newest:
            continue
        if await add_imported_version(session, ci_id, version, write_index=False) is not None:
            added = True
    if added:
        await rebuild_index(session, [ci_id])
    return ci_id


async def add_imported_attachment(
    session: AsyncSession,
    ci_id: int,
    *,
    filename: str,
    content_type: str,
    data: bytes,
    created_at: datetime,
    created_by: int,
) -> int:
    """Attach a file to the CI with the original time and author; returns the attachment id.

    Idempotent per CI: a file with the same name and content (SHA-256) is not stored twice.
    """
    if await session.get(TiqoraCmdbCi, ci_id) is None:
        raise CmdbNotFound(f"ci {ci_id}")
    name = sanitize_filename(filename)
    digest = hashlib.sha256(data).hexdigest()
    existing = (
        await session.execute(
            select(TiqoraCmdbAttachment.id).where(
                TiqoraCmdbAttachment.ci_id == ci_id,
                TiqoraCmdbAttachment.filename == name,
                TiqoraCmdbAttachment.sha256 == digest,
            )
        )
    ).first()
    if existing is not None:
        return int(existing[0])
    row = TiqoraCmdbAttachment(
        ci_id=ci_id,
        filename=name,
        content_type=sanitize_content_type(content_type),
        size_bytes=len(data),
        sha256=digest,
        content=data,
        created_by=created_by,
        created_at=created_at,
    )
    session.add(row)
    await session.flush()
    session.add(_history_row(ci_id, EVENT_ADD, name, None, created_by, created_at))
    await session.flush()
    return int(row.id)


async def delete_ci(session: AsyncSession, ci_id: int, *, user_id: int) -> None:
    """Delete the CI with versions, index, history, links and attachments (no permission
    check; the neighbours' derived incident states are recomputed)."""
    await CiService(session).delete(system_access(user_id), ci_id, enforce_permissions=False)


# --- links and history ------------------------------------------------------------------------


def _num(key: str) -> int:
    return int(key) if key.isascii() and key.isdigit() else 0


async def import_link(
    session: AsyncSession,
    *,
    source_kind: str,
    source_key: str,
    target_kind: str,
    target_key: str,
    link_type_key: str,
    created_by: int,
    created_at: datetime,
) -> bool:
    """Store a link with the given time; ``True`` if it is new, ``False`` if it existed.

    The link type must exist (``UnknownLinkType`` otherwise; invalid types are accepted, an
    import keeps what Znuny had). Undirected types are stored in the same canonical
    orientation as interactive links (CI first; CI to CI: the smaller id first). The ends are
    not checked for existence, and no history row is written (see :func:`import_history`).
    """
    source, target = _end(source_kind, source_key), _end(target_kind, target_key)
    ltype = await LinkTypeService(session).get_by_name(link_type_key, valid_only=False)
    if not ltype.pointed:
        ci_first = source.kind == "ci" and (
            target.kind != "ci" or _num(source.key) <= _num(target.key)
        )
        if not ci_first:
            source, target = target, source

    async def find() -> int | None:
        found = (
            await session.execute(
                select(TiqoraItsmLink.id).where(
                    TiqoraItsmLink.source_kind == source.kind,
                    TiqoraItsmLink.source_key == source.key,
                    TiqoraItsmLink.target_kind == target.kind,
                    TiqoraItsmLink.target_key == target.key,
                    TiqoraItsmLink.link_type == ltype.name,
                )
            )
        ).first()
        return None if found is None else int(found[0])

    if await find() is not None:
        return False
    try:
        async with session.begin_nested():
            session.add(
                TiqoraItsmLink(
                    source_kind=source.kind,
                    source_key=source.key,
                    target_kind=target.kind,
                    target_key=target.key,
                    link_type=ltype.name,
                    created_by=created_by,
                    created_at=created_at,
                )
            )
            await session.flush()
    except IntegrityError:
        if await find() is None:
            raise
        return False
    return True


async def import_history(
    session: AsyncSession, ci_id: int, rows: Sequence[tuple[str, str, int, datetime]]
) -> int:
    """Store history rows ``(event, detail, user_id, created_at)`` of a CI as given.

    Idempotent: a row equal to a stored one (event, detail, author, time) is skipped. Returns
    the number of rows written.
    """
    if not rows:
        return 0
    have = {
        (str(e), str(d), int(u), t)
        for e, d, u, t in (
            await session.execute(
                select(
                    TiqoraCmdbCiHistory.event,
                    TiqoraCmdbCiHistory.detail,
                    TiqoraCmdbCiHistory.created_by,
                    TiqoraCmdbCiHistory.created_at,
                ).where(TiqoraCmdbCiHistory.ci_id == ci_id)
            )
        ).all()
    }
    written = 0
    for event, detail, user_id, at in rows:
        key = (event, detail[:_HISTORY_DETAIL_MAX], user_id, at)
        if key in have:
            continue
        have.add(key)
        session.add(_history_row(ci_id, event, detail, None, user_id, at))
        written += 1
    if written:
        await session.flush()
    return written


async def finish_import(session: AsyncSession, ci_ids: Sequence[int]) -> PropagationResult:
    """Close a run: rebuild the search index of ``ci_ids``, raise each class counter past the
    imported numbers and recompute the derived incident states from these CIs."""
    ids = sorted({int(i) for i in ci_ids})
    if not ids:
        return PropagationResult(0, False)
    await rebuild_index(session, ids)
    numbers: dict[int, str] = {}
    for start in range(0, len(ids), _ID_CHUNK):
        rows = await session.execute(
            select(TiqoraCmdbCi.class_id, TiqoraCmdbCi.number).where(
                TiqoraCmdbCi.id.in_(ids[start : start + _ID_CHUNK])
            )
        )
        for class_id, number in rows.all():
            current = numbers.get(int(class_id))
            # only the highest number of a class matters; equal-length numbers compare as text
            if current is None or (len(number), number) > (len(current), current):
                numbers[int(class_id)] = str(number)
    for class_id, number in numbers.items():
        await bump_counter_for_number(session, class_id, number)
    return await recompute_from(session, ids)


__all__ = [
    "DEFAULT_DEPLOYMENT_KEY",
    "DEFAULT_INCIDENT_KEY",
    "DefinitionImportResult",
    "DefinitionParseError",
    "ImportedVersion",
    "add_definition",
    "add_imported_attachment",
    "add_imported_version",
    "create_imported_ci",
    "delete_ci",
    "finish_import",
    "import_ci",
    "import_history",
    "import_link",
    "latest_version_id",
    "parse_znuny_definition",
    "upsert_catalog_item",
    "upsert_class",
]
