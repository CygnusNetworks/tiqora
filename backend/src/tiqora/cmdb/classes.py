"""CI classes and their versioned definitions (Znuny ``ITSMConfigItem::Class`` + definitions)."""

from __future__ import annotations

import builtins
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Final

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.cmdb.catalog import CatalogService
from tiqora.cmdb.definition import (
    AttributeDef,
    DefinitionIssue,
    dump_definition_json,
    iter_attribute_paths,
    parse_definition_json,
    validate_definition,
)
from tiqora.cmdb.errors import CmdbError, CmdbNotFound
from tiqora.cmdb.templates import CLASS_TEMPLATES
from tiqora.db.tiqora.models import TiqoraCmdbCi, TiqoraCmdbClass, TiqoraCmdbClassDefinition
from tiqora.extensions import Registry
from tiqora.jobs.service import UnknownJobKind, enqueue_job, utc_now

logger = structlog.get_logger(__name__)

CLASS_KEY_RE: Final = re.compile(r"^[a-z][a-z0-9_]{1,49}$")
#: handler: Task 8; rebuilds the search index of one class in batches of 1000
CMDB_REINDEX_KIND: Final = "core.cmdb_reindex"
_DEF_CACHE: Final = "cmdb_definitions"


class ClassKeyInvalid(CmdbError):
    """The class key does not match ``^[a-z][a-z0-9_]{1,49}$`` or an existing key was changed."""


class ClassKeyTaken(CmdbError):
    """Another class already has this key."""


class ClassConflict(CmdbError):
    """``expected_lock_version`` is stale (answered as 409)."""


class DefinitionNotFound(CmdbNotFound):
    """No such definition (or the class has none yet)."""


class TemplateNotFound(CmdbNotFound):
    """No built-in class template with this key."""


class DefinitionInvalid(CmdbError):
    def __init__(self, issues: list[DefinitionIssue]) -> None:
        super().__init__(f"{len(issues)} definition issue(s)")
        self.issues = issues


@dataclass(frozen=True, slots=True)
class ClassData:
    class_key: str
    name: str
    group_id: int
    icon: str | None = None
    number_prefix: str | None = None
    portal_visible: bool = False
    valid: bool = True
    position: int = 0


@dataclass(frozen=True, slots=True)
class ClassRecord:
    id: int
    class_key: str
    name: str
    icon: str | None
    number_prefix: str
    group_id: int
    current_definition_id: int | None
    portal_visible: bool
    position: int
    valid: bool
    lock_version: int
    created_by: int
    created_at: datetime
    changed_by: int
    changed_at: datetime


@dataclass(frozen=True, slots=True)
class DefinitionRecord:
    id: int
    class_id: int
    version: int
    attributes: list[AttributeDef]
    comment: str | None
    created_by: int
    created_at: datetime


def _record(row: TiqoraCmdbClass) -> ClassRecord:
    return ClassRecord(
        id=int(row.id),
        class_key=row.class_key,
        name=row.name,
        icon=row.icon,
        number_prefix=row.number_prefix,
        group_id=int(row.group_id),
        current_definition_id=(
            None if row.current_definition_id is None else int(row.current_definition_id)
        ),
        portal_visible=bool(row.portal_visible),
        position=int(row.position),
        valid=bool(row.valid),
        lock_version=int(row.lock_version),
        created_by=int(row.created_by),
        created_at=row.created_at,
        changed_by=int(row.changed_by),
        changed_at=row.changed_at,
    )


def _definition(row: TiqoraCmdbClassDefinition) -> DefinitionRecord:
    return DefinitionRecord(
        id=int(row.id),
        class_id=int(row.class_id),
        version=int(row.version),
        attributes=parse_definition_json(row.attributes),
        comment=row.comment,
        created_by=int(row.created_by),
        created_at=row.created_at,
    )


def _check_data(data: ClassData) -> None:
    if not CLASS_KEY_RE.match(data.class_key):
        raise ClassKeyInvalid(data.class_key)
    if not 1 <= len(data.name) <= 200:
        raise ValueError("class name must be 1 to 200 characters")


def _searchable_paths(attrs: list[AttributeDef]) -> set[str]:
    return {p for p, a in iter_attribute_paths(attrs) if a.searchable}


class ClassService:
    def __init__(self, session: AsyncSession, *, registry: Registry | None = None) -> None:
        self._s = session
        self._registry = registry

    # --- classes ---------------------------------------------------------------------

    async def list(self, *, include_invalid: bool = False) -> list[ClassRecord]:
        stmt = select(TiqoraCmdbClass).order_by(
            TiqoraCmdbClass.position, TiqoraCmdbClass.name, TiqoraCmdbClass.id
        )
        if not include_invalid:
            stmt = stmt.where(TiqoraCmdbClass.valid.is_(True))
        return [_record(r) for r in (await self._s.execute(stmt)).scalars().all()]

    async def get(self, class_id: int) -> ClassRecord:
        row = await self._s.get(TiqoraCmdbClass, class_id)
        if row is None:
            raise CmdbNotFound(f"class {class_id}")
        return _record(row)

    async def get_by_key(self, key: str) -> ClassRecord:
        row = (
            await self._s.execute(select(TiqoraCmdbClass).where(TiqoraCmdbClass.class_key == key))
        ).scalar_one_or_none()
        if row is None:
            raise CmdbNotFound(f"class {key}")
        return _record(row)

    async def create(self, data: ClassData, *, user_id: int) -> ClassRecord:
        _check_data(data)
        taken = (
            await self._s.execute(
                select(TiqoraCmdbClass.id).where(TiqoraCmdbClass.class_key == data.class_key)
            )
        ).first()
        if taken is not None:
            raise ClassKeyTaken(data.class_key)
        now = utc_now()
        row = TiqoraCmdbClass(
            class_key=data.class_key,
            name=data.name,
            icon=data.icon,
            number_prefix=data.number_prefix or data.class_key.upper()[:15] + "-",
            group_id=data.group_id,
            portal_visible=data.portal_visible,
            valid=data.valid,
            position=data.position,
            created_by=user_id,
            created_at=now,
            changed_by=user_id,
            changed_at=now,
        )
        self._s.add(row)
        await self._s.flush()
        return _record(row)

    async def update(
        self, class_id: int, data: ClassData, *, expected_lock_version: int, user_id: int
    ) -> ClassRecord:
        """Optimistic update; the key is immutable, ``number_prefix=None`` keeps the prefix."""
        _check_data(data)
        current = await self.get(class_id)
        if data.class_key != current.class_key:
            raise ClassKeyInvalid("the class key cannot be changed")
        res = await self._s.execute(
            update(TiqoraCmdbClass)
            .where(
                TiqoraCmdbClass.id == class_id,
                TiqoraCmdbClass.lock_version == expected_lock_version,
            )
            .values(
                name=data.name,
                icon=data.icon,
                number_prefix=data.number_prefix or current.number_prefix,
                group_id=data.group_id,
                portal_visible=data.portal_visible,
                valid=data.valid,
                position=data.position,
                lock_version=TiqoraCmdbClass.lock_version + 1,
                changed_by=user_id,
                changed_at=utc_now(),
            )
            .execution_options(synchronize_session=False)
        )
        if res.rowcount != 1:  # type: ignore[attr-defined]
            raise ClassConflict(f"class {class_id}")
        row = await self._s.get(TiqoraCmdbClass, class_id, populate_existing=True)
        assert row is not None
        return _record(row)

    # --- definitions -----------------------------------------------------------------

    def _cache(self) -> dict[int, DefinitionRecord]:
        cache: dict[int, DefinitionRecord] = self._s.info.setdefault(_DEF_CACHE, {})
        return cache

    async def check(
        self,
        attrs: builtins.list[AttributeDef],
        *,
        extra_class_keys: frozenset[str] = frozenset(),
    ) -> builtins.list[DefinitionIssue]:
        """The validation issues of a definition (empty list = valid); never raises."""
        catalog_classes = set(await CatalogService(self._s).list_classes())
        class_keys = {
            str(k) for k in (await self._s.execute(select(TiqoraCmdbClass.class_key))).scalars()
        }
        return validate_definition(
            attrs,
            known_catalog_classes=catalog_classes,
            known_class_keys=class_keys | extra_class_keys,
        )

    async def _validate(
        self, attrs: builtins.list[AttributeDef], *, extra_class_keys: frozenset[str]
    ) -> None:
        issues = await self.check(attrs, extra_class_keys=extra_class_keys)
        if issues:
            raise DefinitionInvalid(issues)

    async def add_definition(
        self,
        class_id: int,
        attrs: builtins.list[AttributeDef],
        *,
        comment: str | None,
        user_id: int,
        created_at: datetime | None = None,
        make_current: bool = True,
        extra_class_keys: frozenset[str] = frozenset(),
    ) -> DefinitionRecord:
        """Validate and append the next version (``max + 1``) of the class definition.

        The class row is locked for the version number. When the set of searchable attribute
        paths changes and the class has CIs, ``core.cmdb_reindex`` (dedupe ``class:<id>``) is
        queued: the index is always built from the class's current definition. No history row
        is written: the definition list is the history.
        """
        await self._validate(attrs, extra_class_keys=extra_class_keys)
        cls = (
            await self._s.execute(
                select(TiqoraCmdbClass).where(TiqoraCmdbClass.id == class_id).with_for_update()
            )
        ).scalar_one_or_none()
        if cls is None:
            raise CmdbNotFound(f"class {class_id}")
        previous: list[AttributeDef] = []
        if make_current and cls.current_definition_id is not None:
            previous = (await self.definition(int(cls.current_definition_id))).attributes
        version = (
            await self._s.execute(
                select(func.max(TiqoraCmdbClassDefinition.version)).where(
                    TiqoraCmdbClassDefinition.class_id == class_id
                )
            )
        ).scalar_one() or 0
        row = TiqoraCmdbClassDefinition(
            class_id=class_id,
            version=int(version) + 1,
            attributes=dump_definition_json(attrs),
            comment=comment,
            created_by=user_id,
            created_at=created_at or utc_now(),
        )
        self._s.add(row)
        await self._s.flush()
        if make_current:
            cls.current_definition_id = row.id
            cls.changed_by = user_id
            cls.changed_at = utc_now()
            await self._s.flush()
            if _searchable_paths(previous) != _searchable_paths(attrs):
                await self._enqueue_reindex(class_id, user_id)
        rec = _definition(row)
        self._cache()[rec.id] = rec
        return rec

    async def _enqueue_reindex(self, class_id: int, user_id: int) -> None:
        has_cis = (
            await self._s.execute(
                select(TiqoraCmdbCi.id).where(TiqoraCmdbCi.class_id == class_id).limit(1)
            )
        ).first()
        if has_cis is None:
            return
        try:
            await enqueue_job(
                self._s,
                kind=CMDB_REINDEX_KIND,
                payload={"class_id": class_id},
                created_by=user_id,
                dedupe_key=f"class:{class_id}",
                registry=self._registry,
            )
        except UnknownJobKind:
            logger.error("cmdb_reindex_handler_missing", class_id=class_id)

    async def definitions(self, class_id: int) -> builtins.list[DefinitionRecord]:
        rows = await self._s.execute(
            select(TiqoraCmdbClassDefinition)
            .where(TiqoraCmdbClassDefinition.class_id == class_id)
            .order_by(TiqoraCmdbClassDefinition.version)
        )
        out = [_definition(r) for r in rows.scalars().all()]
        self._cache().update({d.id: d for d in out})
        return out

    async def definition(self, definition_id: int) -> DefinitionRecord:
        cached = self._cache().get(definition_id)
        if cached is not None:
            return cached
        row = await self._s.get(TiqoraCmdbClassDefinition, definition_id)
        if row is None:
            raise DefinitionNotFound(f"definition {definition_id}")
        rec = _definition(row)
        self._cache()[rec.id] = rec
        return rec

    async def current_definition(self, class_id: int) -> DefinitionRecord:
        cls = await self.get(class_id)
        if cls.current_definition_id is None:
            raise DefinitionNotFound(f"class {class_id} has no definition")
        return await self.definition(cls.current_definition_id)

    # --- templates -------------------------------------------------------------------

    async def apply_template(self, key: str, *, group_id: int, user_id: int) -> ClassRecord:
        """Insert the template's missing catalog items, then its class with definition 1."""
        tpl = CLASS_TEMPLATES.get(key)
        if tpl is None:
            raise TemplateNotFound(key)
        catalog = CatalogService(self._s)
        existing = (
            await self._s.execute(
                select(TiqoraCmdbClass.id).where(TiqoraCmdbClass.class_key == tpl.key)
            )
        ).first()
        if existing is not None:
            raise ClassKeyTaken(tpl.key)
        for seed in tpl.catalog_items:
            known = {
                i.item_key for i in await catalog.items(seed.catalog_class, include_invalid=True)
            }
            if seed.item_key not in known:
                await catalog.create(
                    seed.catalog_class,
                    seed.item_key,
                    seed.name,
                    functionality=seed.functionality,
                    position=seed.position,
                )
        cls = await self.create(
            ClassData(class_key=tpl.key, name=tpl.name, group_id=group_id, icon=tpl.icon),
            user_id=user_id,
        )
        # templates reference each other (Computer.Location -> location): all template keys count
        await self.add_definition(
            cls.id,
            tpl.attributes,
            comment=None,
            user_id=user_id,
            extra_class_keys=frozenset(CLASS_TEMPLATES),
        )
        return await self.get(cls.id)


__all__ = [
    "CLASS_KEY_RE",
    "CMDB_REINDEX_KIND",
    "ClassConflict",
    "ClassData",
    "ClassKeyInvalid",
    "ClassKeyTaken",
    "ClassRecord",
    "ClassService",
    "DefinitionInvalid",
    "DefinitionNotFound",
    "DefinitionRecord",
    "TemplateNotFound",
]
