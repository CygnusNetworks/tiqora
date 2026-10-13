"""CI write path (D5/D11): create, update, delete, get with versions, locking and history."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Literal

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.cmdb.access import CmdbAccess, require_class_access
from tiqora.cmdb.attachments import attachment_ids_in, bind_staged, check_staged
from tiqora.cmdb.classes import ClassService
from tiqora.cmdb.definition import AttributeDef, iter_attribute_paths, parse_definition_json
from tiqora.cmdb.errors import CmdbError, CmdbNotFound
from tiqora.cmdb.index import write_index
from tiqora.cmdb.links import signal_ticket_link
from tiqora.cmdb.numbering import NumberExhausted, next_ci_number
from tiqora.cmdb.seed import DEPLOYMENT_STATE, INCIDENT_STATE
from tiqora.cmdb.settings import CmdbSettings
from tiqora.cmdb.values import (
    ValueIssue,
    canonical_json,
    check_references,
    collect_reference_paths,
    normalize_values,
    unknown_top_level_keys,
)
from tiqora.db.legacy.user import Users
from tiqora.db.tiqora.models import (
    TiqoraCmdbCatalog,
    TiqoraCmdbCi,
    TiqoraCmdbCiAttr,
    TiqoraCmdbCiHistory,
    TiqoraCmdbCiVersion,
    TiqoraCmdbCiVersionArchive,
    TiqoraCmdbClass,
    TiqoraCmdbClassDefinition,
    TiqoraItsmLink,
)
from tiqora.jobs.service import utc_now

#: set by the propagation module (Task 12): ``await hook(session, ci_ids)`` recomputes the
#: derived incident state of the CIs and everything downstream. ``None`` = no propagation yet.
_recompute_hook: Callable[[AsyncSession, list[int]], Awaitable[None]] | None = None

_NAME_MAX: Final = 250
_DETAIL_MAX: Final = 1000
_VALUE_EVENTS_MAX: Final = 20
_INCIDENT: Final = "incident"
_PAGE_MAX: Final = 100


class CiConflict(CmdbError):
    """``expected_lock_version`` is stale (answered as 409)."""


class UnknownAttributes(CmdbError):
    """The save would drop attribute keys the current definition no longer knows (422)."""

    def __init__(self, keys: list[str]) -> None:
        super().__init__(f"unknown attributes: {', '.join(keys)}")
        self.keys = keys


class InvalidValues(CmdbError):
    def __init__(self, issues: list[ValueIssue]) -> None:
        super().__init__(f"{len(issues)} invalid value(s)")
        self.issues = issues


@dataclass(frozen=True, slots=True)
class CiRecord:
    id: int
    number: str
    class_id: int
    class_key: str
    name: str
    deployment_state_id: int
    incident_state_id: int
    cur_incident_state_id: int
    current_version_id: int | None
    version_count: int
    lock_version: int
    attributes: dict[str, Any]
    definition_id: int | None
    created_by: int
    created_at: datetime
    changed_by: int
    changed_at: datetime


@dataclass(frozen=True, slots=True)
class SaveResult:
    ci: CiRecord
    changed: bool


@dataclass(frozen=True, slots=True)
class VersionRecord:
    """One version with the attribute tree of **its own** definition."""

    id: int
    ci_id: int
    definition_id: int
    definition_version: int
    definition_attrs: list[AttributeDef]
    name: str
    deployment_state_id: int
    incident_state_id: int
    attributes: dict[str, Any]
    change_note: str | None
    created_by: int
    created_at: datetime
    archived: bool


@dataclass(frozen=True, slots=True)
class VersionSummary:
    """A version without its attribute document (list view)."""

    id: int
    ci_id: int
    definition_id: int
    definition_version: int
    name: str
    deployment_state_id: int
    incident_state_id: int
    deployment_state: str
    incident_state: str
    change_note: str | None
    created_by: int
    created_by_name: str | None
    created_at: datetime
    archived: bool


@dataclass(frozen=True, slots=True)
class VersionPage:
    items: list[VersionSummary]
    has_more: bool
    archived_count: int


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    id: int
    ci_id: int
    event: str
    detail: str
    version_id: int | None
    created_by: int
    created_by_name: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class HistoryPage:
    items: list[HistoryEntry]
    has_more: bool


def _page_limit(limit: int) -> int:
    return max(1, min(int(limit), _PAGE_MAX))


def system_access(user_id: int) -> CmdbAccess:
    """Unrestricted access for the process action, jobs and the importer; ``user_id`` is the
    author recorded on versions and history."""
    return CmdbAccess(user_id, True, frozenset(), frozenset())


def _clip(text: str, limit: int = _DETAIL_MAX) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _show(entries: Any) -> str:
    """Compact text of an attribute's entries for history details."""
    if not isinstance(entries, list) or not entries:
        return "–"
    parts: list[str] = []
    for e in entries:
        if not isinstance(e, Mapping):
            continue
        v = e.get("v")
        text = "" if v is None else str(v)
        sub = e.get("s")
        if isinstance(sub, Mapping) and sub:
            inner = "; ".join(f"{k}: {_show(x)}" for k, x in sub.items())
            text = f"{text} ({inner})" if text else f"({inner})"
        parts.append(text or "–")
    return ", ".join(parts) if parts else "–"


def _value_events(old: Mapping[str, Any], new: Mapping[str, Any]) -> list[str]:
    details: list[str] = []
    for key in sorted(set(old) | set(new)):
        if canonical_json({key: old.get(key)}) == canonical_json({key: new.get(key)}):
            continue
        details.append(_clip(f"{key}: {_clip(_show(old.get(key)), 400)} → {_show(new.get(key))}"))
    if len(details) > _VALUE_EVENTS_MAX:
        rest = len(details) - _VALUE_EVENTS_MAX
        details = [*details[:_VALUE_EVENTS_MAX], f"… ({rest} weitere)"]
    return details


def _record(row: TiqoraCmdbCi, class_key: str, version: TiqoraCmdbCiVersion | None) -> CiRecord:
    return CiRecord(
        id=int(row.id),
        number=row.number,
        class_id=int(row.class_id),
        class_key=class_key,
        name=row.name,
        deployment_state_id=int(row.deployment_state_id),
        incident_state_id=int(row.incident_state_id),
        cur_incident_state_id=int(row.cur_incident_state_id),
        current_version_id=None if row.current_version_id is None else int(row.current_version_id),
        version_count=int(row.version_count),
        lock_version=int(row.lock_version),
        attributes={} if version is None else json.loads(version.attributes),
        definition_id=None if version is None else int(version.definition_id),
        created_by=int(row.created_by),
        created_at=row.created_at,
        changed_by=int(row.changed_by),
        changed_at=row.changed_at,
    )


class CiService:
    def __init__(self, session: AsyncSession, *, settings: CmdbSettings | None = None) -> None:
        self._s = session
        self._settings = settings or CmdbSettings.model_validate({})
        self._classes = ClassService(session)

    # --- reading -----------------------------------------------------------------------

    async def _row(self, ci_id: int) -> TiqoraCmdbCi:
        row = (
            await self._s.execute(
                select(TiqoraCmdbCi)
                .where(TiqoraCmdbCi.id == ci_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if row is None:
            raise CmdbNotFound(f"ci {ci_id}")
        return row

    async def _version(self, version_id: int | None) -> TiqoraCmdbCiVersion | None:
        if version_id is None:
            return None
        return (
            await self._s.execute(
                select(TiqoraCmdbCiVersion)
                .where(TiqoraCmdbCiVersion.id == version_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()

    async def _class_key(self, class_id: int) -> str:
        key = (
            await self._s.execute(
                select(TiqoraCmdbClass.class_key).where(TiqoraCmdbClass.id == class_id)
            )
        ).scalar_one_or_none()
        if key is None:
            raise CmdbNotFound(f"class {class_id}")
        return str(key)

    async def _load(
        self, access: CmdbAccess, row: TiqoraCmdbCi, perm: Literal["ro", "rw"], enforce: bool
    ) -> CiRecord:
        if enforce:
            await require_class_access(self._s, access, int(row.class_id), perm)
        return _record(
            row,
            await self._class_key(int(row.class_id)),
            await self._version(
                None if row.current_version_id is None else int(row.current_version_id)
            ),
        )

    async def get(
        self, access: CmdbAccess, ci_id: int, *, enforce_permissions: bool = True
    ) -> CiRecord:
        return await self._load(access, await self._row(ci_id), "ro", enforce_permissions)

    async def get_by_number(
        self, access: CmdbAccess, number: str, *, enforce_permissions: bool = True
    ) -> CiRecord:
        found = (
            await self._s.execute(select(TiqoraCmdbCi.id).where(TiqoraCmdbCi.number == number))
        ).scalar_one_or_none()
        if found is None:
            raise CmdbNotFound(f"ci {number}")
        return await self.get(access, int(found), enforce_permissions=enforce_permissions)

    # --- shared helpers ----------------------------------------------------------------

    async def _catalog(self, ids: set[int]) -> dict[int, tuple[str, str, str | None, bool]]:
        """id -> (catalog_class, name, functionality, valid)."""
        if not ids:
            return {}
        rows = await self._s.execute(
            select(
                TiqoraCmdbCatalog.id,
                TiqoraCmdbCatalog.catalog_class,
                TiqoraCmdbCatalog.name,
                TiqoraCmdbCatalog.functionality,
                TiqoraCmdbCatalog.valid,
            ).where(TiqoraCmdbCatalog.id.in_(sorted(ids)))
        )
        return {int(i): (str(c), str(n), f, bool(v)) for i, c, n, f, v in rows.all()}

    async def _check_states(
        self, deployment_id: int, incident_id: int, *, keep: set[int]
    ) -> dict[int, tuple[str, str, str | None, bool]]:
        cat = await self._catalog({deployment_id, incident_id})
        issues: list[ValueIssue] = []
        for field, cid, cls in (
            ("deployment_state_id", deployment_id, DEPLOYMENT_STATE),
            ("incident_state_id", incident_id, INCIDENT_STATE),
        ):
            item = cat.get(cid)
            if item is None or item[0] != cls or (not item[3] and cid not in keep):
                issues.append(ValueIssue(field, "unknown_reference", "Ungültiger Status."))
        if issues:
            raise InvalidValues(issues)
        return cat

    @staticmethod
    def _check_name(name: str) -> str:
        name = name.strip()
        if not name:
            raise InvalidValues([ValueIssue("name", "required", "Pflichtfeld.")])
        if len(name) > _NAME_MAX:
            raise InvalidValues([ValueIssue("name", "too_long", f"Höchstens {_NAME_MAX} Zeichen.")])
        return name

    async def _validate_refs(
        self,
        attrs: list[AttributeDef],
        values: Mapping[str, Any],
        *,
        ci_id: int | None,
        user_id: int,
        already: Mapping[str, Any] | None = None,
    ) -> None:
        """References that are new compared with ``already`` must exist (a reference that was
        stored earlier and has since vanished does not block unrelated edits)."""
        refs = collect_reference_paths(attrs, values)
        if already:
            old = collect_reference_paths(attrs, already)
            refs = {
                p: {v: e for v, e in found.items() if v not in old.get(p, {})}
                for p, found in refs.items()
            }
            refs = {p: f for p, f in refs.items() if f}
        if not refs:
            return
        issues = await check_references(
            self._s,
            refs,
            attrs_by_path=dict(iter_attribute_paths(attrs)),
            ci_id=ci_id,
            user_id=user_id,
        )
        if issues:
            raise InvalidValues(issues)

    def _history(
        self,
        ci_id: int,
        event: str,
        detail: str,
        version_id: int | None,
        user_id: int,
        at: datetime,
    ) -> None:
        self._s.add(
            TiqoraCmdbCiHistory(
                ci_id=ci_id,
                event=event,
                detail=_clip(detail),
                version_id=version_id,
                created_by=user_id,
                created_at=at,
            )
        )

    async def _recompute(self, ci_ids: list[int]) -> None:
        hook = _recompute_hook
        if hook is not None and ci_ids:
            await hook(self._s, ci_ids)

    # --- create ------------------------------------------------------------------------

    async def create(
        self,
        access: CmdbAccess,
        *,
        class_id: int,
        name: str,
        deployment_state_id: int,
        incident_state_id: int,
        attributes: Mapping[str, Any],
        change_note: str | None = None,
        attachment_ids: Sequence[int] = (),  # staged uploads of ``access.user_id``
        recompute: bool = True,
        enforce_permissions: bool = True,
    ) -> CiRecord:
        if enforce_permissions:
            cls = await require_class_access(self._s, access, class_id, "rw")
        else:
            found = await self._s.get(TiqoraCmdbClass, class_id)
            if found is None:
                raise CmdbNotFound(f"class {class_id}")
            cls = found
        if not cls.valid:
            raise InvalidValues(
                [ValueIssue("class_id", "class_invalid", "Die Klasse ist nicht mehr gültig.")]
            )
        definition = await self._classes.current_definition(class_id)
        name = self._check_name(name)
        cat = await self._check_states(deployment_state_id, incident_state_id, keep=set())
        known = {a.key for a in definition.attributes}
        raw = {k: v for k, v in attributes.items() if k in known}  # unknown keys are dropped
        values, issues = normalize_values(definition.attributes, raw)
        if issues:
            raise InvalidValues(issues)
        await self._validate_refs(definition.attributes, values, ci_id=None, user_id=access.user_id)
        staged = list(dict.fromkeys(attachment_ids))
        if staged:
            issues = await check_staged(self._s, access.user_id, staged)
            if issues:
                raise InvalidValues(issues)
        number = await next_ci_number(self._s, class_id)
        now = utc_now()
        uid = access.user_id
        row = TiqoraCmdbCi(
            number=number,
            class_id=class_id,
            name=name,
            deployment_state_id=deployment_state_id,
            incident_state_id=incident_state_id,
            cur_incident_state_id=incident_state_id,
            current_version_id=None,
            version_count=0,
            lock_version=1,
            created_by=uid,
            created_at=now,
            changed_by=uid,
            changed_at=now,
        )
        self._s.add(row)
        await self._s.flush()
        version = TiqoraCmdbCiVersion(
            ci_id=row.id,
            definition_id=definition.id,
            name=name,
            deployment_state_id=deployment_state_id,
            incident_state_id=incident_state_id,
            attributes=canonical_json(values),
            change_note=change_note,
            created_by=uid,
            created_at=now,
        )
        self._s.add(version)
        await self._s.flush()
        await self._s.execute(
            update(TiqoraCmdbCi)
            .where(TiqoraCmdbCi.id == row.id)
            .values(current_version_id=version.id, version_count=1)
            .execution_options(synchronize_session=False)
        )
        await write_index(self._s, int(row.id), definition.attributes, values)
        self._history(int(row.id), "ConfigItemCreate", number, None, uid, now)
        self._history(int(row.id), "VersionCreate", "1", int(version.id), uid, now)
        await bind_staged(
            self._s,
            uid,
            int(row.id),
            [*staged, *sorted(attachment_ids_in(definition.attributes, values))],
        )
        await self._s.flush()
        if recompute and cat[incident_state_id][2] == _INCIDENT:
            await self._recompute([int(row.id)])
        return _record(
            await self._row(int(row.id)),
            cls.class_key,
            await self._version(int(version.id)),
        )

    # --- update ------------------------------------------------------------------------

    async def update(
        self,
        access: CmdbAccess,
        ci_id: int,
        *,
        expected_lock_version: int | None,
        name: str | None = None,
        deployment_state_id: int | None = None,
        incident_state_id: int | None = None,
        attributes: Mapping[str, Any] | None = None,
        attributes_patch: Mapping[str, Any] | None = None,
        change_note: str | None = None,
        drop_unknown: bool = False,
        recompute: bool = True,
        enforce_permissions: bool = True,
    ) -> SaveResult:
        if attributes is not None and attributes_patch is not None:
            raise ValueError("pass either attributes or attributes_patch, not both")
        row = await self._row(ci_id)
        if enforce_permissions:
            await require_class_access(self._s, access, int(row.class_id), "rw")
        if expected_lock_version is not None and expected_lock_version != int(row.lock_version):
            raise CiConflict(f"ci {ci_id}")
        class_key = await self._class_key(int(row.class_id))
        current = await self._version(
            None if row.current_version_id is None else int(row.current_version_id)
        )
        old_values: dict[str, Any] = {} if current is None else json.loads(current.attributes)
        definition = await self._classes.current_definition(int(row.class_id))
        attrs = definition.attributes

        new_name = row.name if name is None else self._check_name(name)
        new_dep = (
            int(row.deployment_state_id) if deployment_state_id is None else deployment_state_id
        )
        new_inc = int(row.incident_state_id) if incident_state_id is None else incident_state_id
        cat = await self._check_states(
            new_dep,
            new_inc,
            keep={int(row.deployment_state_id), int(row.incident_state_id)},
        )
        old_cat = await self._catalog({int(row.incident_state_id)})

        new_values = old_values
        if attributes is not None:
            new_values = await self._full_values(
                attrs, old_values, attributes, interactive=enforce_permissions, drop=drop_unknown
            )
        elif attributes_patch is not None:
            new_values = self._patched_values(attrs, old_values, attributes_patch)
        if attributes is not None or attributes_patch is not None:
            await self._validate_refs(
                attrs, new_values, ci_id=ci_id, user_id=access.user_id, already=old_values
            )

        if (
            current is not None
            and new_name == row.name
            and new_dep == int(row.deployment_state_id)
            and new_inc == int(row.incident_state_id)
            and canonical_json(new_values) == canonical_json(old_values)
        ):
            return SaveResult(_record(row, class_key, current), False)

        uid = access.user_id
        now = utc_now()
        new_func = cat[new_inc][2]
        old_func = old_cat.get(int(row.incident_state_id), ("", "", None, True))[2]
        cur = int(row.cur_incident_state_id)
        if new_inc != int(row.incident_state_id) and (
            new_func != old_func or cur == int(row.incident_state_id)
        ):
            cur = new_inc
        res = await self._s.execute(
            update(TiqoraCmdbCi)
            .where(TiqoraCmdbCi.id == ci_id, TiqoraCmdbCi.lock_version == int(row.lock_version))
            .values(
                name=new_name,
                deployment_state_id=new_dep,
                incident_state_id=new_inc,
                cur_incident_state_id=cur,
                lock_version=TiqoraCmdbCi.lock_version + 1,
                changed_by=uid,
                changed_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        if res.rowcount != 1:  # type: ignore[attr-defined]
            raise CiConflict(f"ci {ci_id}")
        version = TiqoraCmdbCiVersion(
            ci_id=ci_id,
            definition_id=definition.id,
            name=new_name,
            deployment_state_id=new_dep,
            incident_state_id=new_inc,
            attributes=canonical_json(new_values),
            change_note=change_note,
            created_by=uid,
            created_at=now,
        )
        self._s.add(version)
        await self._s.flush()
        count = int(row.version_count) + 1
        await self._s.execute(
            update(TiqoraCmdbCi)
            .where(TiqoraCmdbCi.id == ci_id)
            .values(current_version_id=version.id, version_count=TiqoraCmdbCi.version_count + 1)
            .execution_options(synchronize_session=False)
        )
        await write_index(self._s, ci_id, attrs, new_values)
        vid = int(version.id)
        self._history(ci_id, "VersionCreate", str(count), vid, uid, now)
        if new_name != row.name:
            self._history(ci_id, "NameUpdate", f"{row.name} → {new_name}", vid, uid, now)
        names = await self._catalog(
            {int(row.deployment_state_id), new_dep, int(row.incident_state_id), new_inc}
        )
        if new_dep != int(row.deployment_state_id):
            self._history(
                ci_id,
                "DeploymentStateUpdate",
                f"{names[int(row.deployment_state_id)][1]} → {names[new_dep][1]}",
                vid,
                uid,
                now,
            )
        if new_inc != int(row.incident_state_id):
            self._history(
                ci_id,
                "IncidentStateUpdate",
                f"{names[int(row.incident_state_id)][1]} → {names[new_inc][1]}",
                vid,
                uid,
                now,
            )
        for detail in _value_events(old_values, new_values):
            self._history(ci_id, "ValueUpdate", detail, vid, uid, now)
        await bind_staged(self._s, uid, ci_id, sorted(attachment_ids_in(attrs, new_values)))
        await self._s.flush()
        if recompute and new_func != old_func:
            await self._recompute([ci_id])
        return SaveResult(
            _record(await self._row(ci_id), class_key, await self._version(vid)), True
        )

    async def _full_values(
        self,
        attrs: list[AttributeDef],
        old_values: Mapping[str, Any],
        incoming: Mapping[str, Any],
        *,
        interactive: bool,
        drop: bool,
    ) -> dict[str, Any]:
        """Full replacement. Interactive saves must confirm (``drop_unknown``) that keys the
        definition no longer knows are lost; bulk/import/process saves carry them forward."""
        unknown_old = unknown_top_level_keys(attrs, old_values)
        unknown_new = unknown_top_level_keys(attrs, incoming)
        carried: dict[str, Any] = {}
        if interactive:
            lost = sorted(set(unknown_old) | set(unknown_new))
            if lost and not drop:
                raise UnknownAttributes(lost)
        else:
            carried = {k: old_values[k] for k in unknown_old}
        known = {a.key for a in attrs}
        values, issues = normalize_values(attrs, {k: v for k, v in incoming.items() if k in known})
        if issues:
            raise InvalidValues(issues)
        return {**values, **carried}

    @staticmethod
    def _patched_values(
        attrs: list[AttributeDef], old_values: Mapping[str, Any], patch: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Top-level keys of ``patch`` replace the stored entries; everything else (unknown
        keys included) is carried forward unchanged and is not re-validated."""
        known = {a.key for a in attrs}
        issues = [
            ValueIssue(k, "unknown_key", "Unbekanntes Attribut.") for k in patch if k not in known
        ]
        if issues:
            raise InvalidValues(issues)
        values, issues = normalize_values([a for a in attrs if a.key in patch], dict(patch))
        if issues:
            raise InvalidValues(issues)
        merged = {k: v for k, v in old_values.items() if k not in patch}
        merged.update(values)
        return merged

    # --- versions and history ----------------------------------------------------------

    async def _readable_ci(self, access: CmdbAccess, ci_id: int, enforce: bool) -> TiqoraCmdbCi:
        row = await self._row(ci_id)
        if enforce:
            await require_class_access(self._s, access, int(row.class_id), "ro")
        return row

    async def _definitions(self, ids: set[int]) -> dict[int, tuple[int, list[AttributeDef]]]:
        """definition id -> (version, attribute tree)."""
        if not ids:
            return {}
        rows = await self._s.execute(
            select(
                TiqoraCmdbClassDefinition.id,
                TiqoraCmdbClassDefinition.version,
                TiqoraCmdbClassDefinition.attributes,
            ).where(TiqoraCmdbClassDefinition.id.in_(sorted(ids)))
        )
        return {int(i): (int(v), parse_definition_json(a)) for i, v, a in rows.all()}

    async def _user_names(self, ids: set[int]) -> dict[int, str]:
        if not ids:
            return {}
        rows = await self._s.execute(
            select(Users.id, Users.first_name, Users.last_name).where(Users.id.in_(sorted(ids)))
        )
        return {int(i): f"{f or ''} {ln or ''}".strip() for i, f, ln in rows.all()}

    async def get_version(
        self,
        access: CmdbAccess,
        ci_id: int,
        version_id: int,
        *,
        enforce_permissions: bool = True,
    ) -> VersionRecord:
        """A version of the CI (hot table first, then the archive); another CI's version id is
        answered as not found."""
        await self._readable_ci(access, ci_id, enforce_permissions)
        hot = (
            await self._s.execute(
                select(TiqoraCmdbCiVersion).where(
                    TiqoraCmdbCiVersion.id == version_id, TiqoraCmdbCiVersion.ci_id == ci_id
                )
            )
        ).scalar_one_or_none()
        row: TiqoraCmdbCiVersion | TiqoraCmdbCiVersionArchive | None = hot
        if row is None:
            row = (
                await self._s.execute(
                    select(TiqoraCmdbCiVersionArchive).where(
                        TiqoraCmdbCiVersionArchive.id == version_id,
                        TiqoraCmdbCiVersionArchive.ci_id == ci_id,
                    )
                )
            ).scalar_one_or_none()
        if row is None:
            raise CmdbNotFound(f"version {version_id}")
        definitions = await self._definitions({int(row.definition_id)})
        def_version, def_attrs = definitions.get(int(row.definition_id), (0, []))
        return VersionRecord(
            id=int(row.id),
            ci_id=int(row.ci_id),
            definition_id=int(row.definition_id),
            definition_version=def_version,
            definition_attrs=def_attrs,
            name=row.name,
            deployment_state_id=int(row.deployment_state_id),
            incident_state_id=int(row.incident_state_id),
            attributes=json.loads(row.attributes),
            change_note=row.change_note,
            created_by=int(row.created_by),
            created_at=row.created_at,
            archived=hot is None,
        )

    async def list_versions(
        self,
        access: CmdbAccess,
        ci_id: int,
        *,
        before_id: int | None = None,
        limit: int = 50,
        include_archived: bool = False,
        enforce_permissions: bool = True,
    ) -> VersionPage:
        """Keyset page, newest first (``before_id`` is exclusive). ``include_archived`` merges
        the archive table (same id space); ``archived_count`` is always the archive size."""
        await self._readable_ci(access, ci_id, enforce_permissions)
        size = _page_limit(limit)
        found: list[tuple[Any, bool]] = []
        for model, archived in (
            (TiqoraCmdbCiVersion, False),
            *(((TiqoraCmdbCiVersionArchive, True),) if include_archived else ()),
        ):
            stmt = (
                select(
                    model.id,
                    model.definition_id,
                    model.name,
                    model.deployment_state_id,
                    model.incident_state_id,
                    model.change_note,
                    model.created_by,
                    model.created_at,
                )
                .where(model.ci_id == ci_id)
                .order_by(model.id.desc())
                .limit(size + 1)
            )
            if before_id is not None:
                stmt = stmt.where(model.id < before_id)
            found.extend((r, archived) for r in (await self._s.execute(stmt)).all())
        found.sort(key=lambda item: int(item[0].id), reverse=True)
        has_more = len(found) > size
        found = found[:size]
        archived_count = int(
            (
                await self._s.execute(
                    select(func.count())
                    .select_from(TiqoraCmdbCiVersionArchive)
                    .where(TiqoraCmdbCiVersionArchive.ci_id == ci_id)
                )
            ).scalar_one()
        )
        definitions = await self._definitions({int(r.definition_id) for r, _ in found})
        states = await self._catalog(
            {int(r.deployment_state_id) for r, _ in found}
            | {int(r.incident_state_id) for r, _ in found}
        )
        users = await self._user_names({int(r.created_by) for r, _ in found})
        items = [
            VersionSummary(
                id=int(r.id),
                ci_id=ci_id,
                definition_id=int(r.definition_id),
                definition_version=definitions.get(int(r.definition_id), (0, []))[0],
                name=r.name,
                deployment_state_id=int(r.deployment_state_id),
                incident_state_id=int(r.incident_state_id),
                deployment_state=states.get(int(r.deployment_state_id), ("", "", None, True))[1],
                incident_state=states.get(int(r.incident_state_id), ("", "", None, True))[1],
                change_note=r.change_note,
                created_by=int(r.created_by),
                created_by_name=users.get(int(r.created_by)),
                created_at=r.created_at,
                archived=archived,
            )
            for r, archived in found
        ]
        return VersionPage(items=items, has_more=has_more, archived_count=archived_count)

    async def list_history(
        self,
        access: CmdbAccess,
        ci_id: int,
        *,
        before_id: int | None = None,
        limit: int = 50,
        enforce_permissions: bool = True,
    ) -> HistoryPage:
        """History events, newest first (keyset on ``before_id``, exclusive)."""
        await self._readable_ci(access, ci_id, enforce_permissions)
        size = _page_limit(limit)
        stmt = (
            select(TiqoraCmdbCiHistory)
            .where(TiqoraCmdbCiHistory.ci_id == ci_id)
            .order_by(TiqoraCmdbCiHistory.id.desc())
            .limit(size + 1)
        )
        if before_id is not None:
            stmt = stmt.where(TiqoraCmdbCiHistory.id < before_id)
        rows = list((await self._s.execute(stmt)).scalars().all())
        has_more = len(rows) > size
        rows = rows[:size]
        users = await self._user_names({int(r.created_by) for r in rows})
        return HistoryPage(
            items=[
                HistoryEntry(
                    id=int(r.id),
                    ci_id=int(r.ci_id),
                    event=r.event,
                    detail=r.detail,
                    version_id=None if r.version_id is None else int(r.version_id),
                    created_by=int(r.created_by),
                    created_by_name=users.get(int(r.created_by)),
                    created_at=r.created_at,
                )
                for r in rows
            ],
            has_more=has_more,
        )

    # --- delete ------------------------------------------------------------------------

    async def delete(
        self, access: CmdbAccess, ci_id: int, *, enforce_permissions: bool = True
    ) -> None:
        row = await self._row(ci_id)
        if enforce_permissions:
            await require_class_access(self._s, access, int(row.class_id), "rw")
        key = str(ci_id)
        links = (
            await self._s.execute(
                select(
                    TiqoraItsmLink.source_kind,
                    TiqoraItsmLink.source_key,
                    TiqoraItsmLink.target_kind,
                    TiqoraItsmLink.target_key,
                ).where(
                    or_(
                        (TiqoraItsmLink.source_kind == "ci") & (TiqoraItsmLink.source_key == key),
                        (TiqoraItsmLink.target_kind == "ci") & (TiqoraItsmLink.target_key == key),
                    )
                )
            )
        ).all()
        neighbours: set[int] = set()
        tickets: set[int] = set()
        for sk, skey, tk, tkey in links:
            for kind, other in ((sk, skey), (tk, tkey)):
                if kind == "ci" and str(other).isdigit() and int(other) != ci_id:
                    neighbours.add(int(other))
                elif kind == "ticket" and str(other).isdigit():
                    tickets.add(int(other))
        for stmt in (
            delete(TiqoraItsmLink).where(
                or_(
                    (TiqoraItsmLink.source_kind == "ci") & (TiqoraItsmLink.source_key == key),
                    (TiqoraItsmLink.target_kind == "ci") & (TiqoraItsmLink.target_key == key),
                )
            ),
            delete(TiqoraCmdbCiHistory).where(TiqoraCmdbCiHistory.ci_id == ci_id),
            delete(TiqoraCmdbCiVersionArchive).where(TiqoraCmdbCiVersionArchive.ci_id == ci_id),
            delete(TiqoraCmdbCiAttr).where(TiqoraCmdbCiAttr.ci_id == ci_id),
            delete(TiqoraCmdbCiVersion).where(TiqoraCmdbCiVersion.ci_id == ci_id),
            delete(TiqoraCmdbCi).where(TiqoraCmdbCi.id == ci_id),
        ):
            await self._s.execute(stmt.execution_options(synchronize_session=False))
        self._s.expunge(row)
        for ticket_id in sorted(tickets):
            await signal_ticket_link(self._s, "LinkDelete", ticket_id, key)
        await self._recompute(sorted(neighbours))


__all__ = [
    "CiConflict",
    "CiRecord",
    "CiService",
    "HistoryEntry",
    "HistoryPage",
    "InvalidValues",
    "NumberExhausted",
    "SaveResult",
    "UnknownAttributes",
    "VersionPage",
    "VersionRecord",
    "VersionSummary",
    "system_access",
]
