"""Facades of the ITSM importer: the only module that reaches into itsm1 and cmdb internals.

Contract table (C1-C10 of the plan, checked against roadmap-wave1 7bc810ec2; reviewer checklist):

C1  ITSM mode          ``tiqora.itsm.mode.get_itsm_mode(session, fresh=True).mode`` (stored
                       ``itsm.mode`` or the auto default): ``ItsmModeFacade.effective_mode``.
                       The plan's ``effective_itsm_mode`` name does not exist.
C2  Mode A->B guard    itsm1 ``mode.allowed_transitions`` / ``ModeTransitionNotAllowed`` (409
                       ``mode_transition_not_allowed``); no guard of this plan (R6).
    Switch / rollback  ``mode.switch_to_tiqora`` / ``mode.rollback_to_znuny`` (write ``itsm.mode``,
                       ``itsm.mode_meta``, ``itsm.switch``; flush only), ``mode.is_switched``.
    Detection          ``znuny_adapter.detect.detect_znuny_itsm(...).core_installed`` with
                       ``use_cache=False``: ``ItsmModeFacade.znuny_itsm_present``.
C3  Znuny read adapter ``znuny_adapter.reader.ZnunyItsmReader`` via ``get_reader``:
                       ``iter_general_catalog``, ``iter_class_definitions``, ``iter_config_items``,
                       ``get_cis``, ``versions_for`` + ``version_xml_data`` (raw XMLData, R10).
                       Added by this plan in ``importer/source_extra.py``: ``links_for_cis``,
                       ``ci_count``, ``max_ci_change_time``, ``count_cis_changed_since``,
                       ``read_ci_attachments`` (``virtual_fs``, Task 11).
                       ``change_counts`` (Task 12): ``source_extra.count_changes``;
                       ``cip_rows`` / ``service_itsm_rows``: ``cip_allocate`` /
                       ``service_itsm_columns`` (the delegated steps, ITSMCore alone suffices).
C4  Definition parsing ``tiqora.cmdb.znuny_definition.parse_znuny_definition`` (R8; this plan has
                       no parser); used by Tasks 3 and 8, not by a facade.
C5  CMDB tables        ``TiqoraCmdb*`` / ``TiqoraItsmLink`` in ``tiqora.db.tiqora.models`` (columns
                       ``source_key`` / ``target_key`` / ``created_at``, R9); read here only.
C6  Class schema JSON  cmdb ``AttributeDef`` list (``cmdb.definition``); ``CiView.schema`` is its
                       JSON dump. The Znuny->cmdb converter is cmdb's ``znuny_definition``.
C7  CI attribute JSON  cmdb D4 ``{"Key": [{"v": ..., "s": {...}}]}`` (R9); conversion from Znuny
                       ``XMLData`` is Task 4 (``tiqora.itsm.znuny_format``), not delegated.
C8  CMDB write service ``tiqora.cmdb.importer_api`` 1:1: ``upsert_catalog_item``, ``upsert_class``,
                       ``add_definition(skip_if_equal=True)``, ``create_imported_ci``,
                       ``add_imported_version`` (``None`` = unchanged), ``latest_version_id``,
                       ``add_imported_attachment``, ``import_link``, ``import_history``,
                       ``delete_ci``, ``finish_import``. Link types: itsm1
                       ``link_types.sync_link_type_rows`` (SysConfig driven, origin ``znuny``).
                       Nothing is added to ``tiqora/cmdb`` (no ``import_api.py``).
C9  CMDB read          ``cmdb.access`` (``load_access``, ``readable_class_ids``,
                       ``writable_class_ids``), ``CiService.get`` / ``get_version``,
                       ``cmdb.search.search_cis``, ``ClassService``, ``CatalogService``,
                       ``cmdb.impact.impact_graph`` + ``affected``.
C10 REST links / key   not used by the importer (Task 27 consumes ``POST /api/v1/cmdb/links``).

A contract change of an owner is absorbed here; importer, GI, MCP and action code import only
:mod:`tiqora.itsm.importer.ports` and :func:`get_ports`.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.cmdb import importer_api
from tiqora.cmdb.access import load_access, readable_class_ids, writable_class_ids
from tiqora.cmdb.catalog import CatalogService
from tiqora.cmdb.classes import ClassService
from tiqora.cmdb.definition import AttributeDef, dump_definition_json
from tiqora.cmdb.errors import CmdbNotFound
from tiqora.cmdb.impact import affected, impact_graph
from tiqora.cmdb.schemas import CiSearchIn
from tiqora.cmdb.search import search_cis
from tiqora.cmdb.seed import DEPLOYMENT_STATE, INCIDENT_STATE
from tiqora.cmdb.service import CiService, system_access
from tiqora.cmdb.settings import load_cmdb_settings
from tiqora.db.tiqora.models import (
    TiqoraCmdbAttachment,
    TiqoraCmdbCatalog,
    TiqoraCmdbCi,
    TiqoraCmdbClass,
)
from tiqora.filters.model import AllOf, Condition, Filter, FilterNode
from tiqora.itsm import mode as itsm_mode
from tiqora.itsm.importer import source_extra
from tiqora.itsm.importer.ports import (
    EPOCH,
    CatalogView,
    CiQuery,
    CiView,
    CmdbClassView,
    CmdbReadPort,
    CmdbWritePort,
    ImpactView,
    ImportSource,
    ItsmModeName,
    ItsmModePort,
    Ports,
    ZnunyAttachment,
    ZnunyCatalogItem,
    ZnunyCi,
    ZnunyCiVersion,
    ZnunyClassDefinition,
    ZnunyLink,
    ZnunySourceInfo,
)
from tiqora.itsm.link_types import sync_link_type_rows
from tiqora.itsm.models import TiqoraItsmServiceMeta
from tiqora.itsm.priority import InvalidMatrix as InvalidMatrix
from tiqora.itsm.priority import MatrixCell, replace_matrix_from_import
from tiqora.itsm.services import ServiceMetaIn, save_service_meta
from tiqora.itsm.services import ServiceNotFound as ServiceNotFound
from tiqora.itsm.znuny_adapter.detect import detect_znuny_itsm
from tiqora.itsm.znuny_adapter.profiles import PROFILES
from tiqora.itsm.znuny_adapter.reader import (
    CipRow,
    ServiceItsmRow,
    ZnunyItsmReader,
    reader_for_info,
)
from tiqora.itsm.znuny_format.definition import definition_format, parse_definition_with_issues
from tiqora.itsm.znuny_format.xmldata import xmldata_to_attributes

#: author recorded when Znuny stored no creator (the root agent)
_ROOT_USER = 1
_SEARCH_PAGE = 200

#: the only test hooks (Task 12): a replacement source for the pipeline, and the answer of
#: ``ItsmModeFacade.znuny_itsm_present``; Task 21 adds ``mode_a_override``; production code never
#: sets them
source_override: ImportSource | None = None
znuny_present_override: bool | None = None
#: Task 21 (GI in Mode A): a replacement :class:`ModeAReader`; tests only
mode_a_override: ModeAReader | None = None


def _author(value: int | None) -> int:
    return _ROOT_USER if value is None else int(value)


def _xml_node(xml_hash: Sequence[Any]) -> Mapping[str, Any]:
    """The ``Version`` node of ``build_xml_hash`` output: ``{Key: [{"Content": ...}, ...]}``."""
    root: Any = None
    for item in xml_hash[:2][::-1]:  # Znuny keeps the root at [1], StatsGet-style data at [0]
        if isinstance(item, dict):
            root = item
            break
    versions = root.get("Version") if isinstance(root, dict) else None
    if isinstance(versions, list):
        for item in versions:
            if isinstance(item, dict):
                return item
    return {}


def _group_id(raw: str | None) -> int | None:
    """The ``Permission`` preference of a CI class (Znuny ``ItemClassGroupCheck``): a group id."""
    text = (raw or "").strip()
    return int(text) if text.isdigit() else None


class ZnunyAdapterSource:
    """:class:`ImportSource` on itsm1's Znuny adapter plus ``source_extra``."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def _reader(self) -> ZnunyItsmReader:
        # fresh detection: an import must not run on a 60 s old picture of the packages
        return reader_for_info(await detect_znuny_itsm(self._s, use_cache=False))

    async def _core_reader(self) -> ZnunyItsmReader:
        """A reader for what ITSMCore alone offers (catalog, matrix, service columns)."""
        d = await detect_znuny_itsm(self._s, use_cache=False)
        if d.core_installed and d.profile is not None:
            return ZnunyItsmReader(PROFILES[d.profile])
        return reader_for_info(d)  # raises the matching CiUnavailable

    async def info(self) -> ZnunySourceInfo:
        d = await detect_znuny_itsm(self._s, use_cache=False)
        return ZnunySourceInfo(
            core_installed=d.core_installed,
            cmdb_installed=d.cmdb_installed,
            profile=d.profile,
            unsupported_reason=d.unsupported_reason,
        )

    async def profile(self) -> str | None:
        info = await self.info()
        return info.profile if info.core_installed else None

    async def catalog_items(self) -> list[ZnunyCatalogItem]:
        reader = await self._core_reader()
        return [
            ZnunyCatalogItem(
                id=r.id,
                catalog_class=r.catalog_class,
                name=r.name,
                valid=r.valid_id == 1,
                comment=r.comments,
                functionality=r.preferences.get("Functionality") or None,
                permission_group=_group_id(r.preferences.get("Permission")),
            )
            for r in await reader.iter_general_catalog(self._s)
        ]

    async def class_definitions(self) -> list[ZnunyClassDefinition]:
        reader = await self._reader()
        return [
            ZnunyClassDefinition(
                id=r.id,
                class_id=r.class_id,
                version=r.version,
                raw=r.text,
                created_at=r.created_at or EPOCH,
                created_by=_author(r.created_by),
            )
            for r in await reader.iter_class_definitions(self._s)
        ]

    async def ci_count(self) -> int:
        await self._reader()  # same availability error as every other read
        return await source_extra.ci_count(self._s)

    async def ci_ids_page(self, after_id: int, limit: int) -> list[int]:
        reader = await self._reader()
        return [
            r.id for r in await reader.iter_config_items(self._s, after_id=after_id, limit=limit)
        ]

    async def cis(self, ids: Sequence[int]) -> list[ZnunyCi]:
        reader = await self._reader()
        rows = await reader.get_cis(self._s, ids)
        return [
            ZnunyCi(
                id=r.id,
                number=r.number,
                class_id=r.class_id,
                last_version_id=r.last_version_id,
                deployment_state_id=r.cur_depl_state_id,
                incident_state_id=r.cur_inci_state_id,
                created_at=r.created_at,
                created_by=_author(r.created_by),
                changed_at=r.changed_at,
                changed_by=_author(r.changed_by),
            )
            for _, r in sorted(rows.items())
        ]

    async def versions(self, ci_ids: Sequence[int]) -> dict[int, list[ZnunyCiVersion]]:
        reader = await self._reader()
        by_ci = await reader.versions_for(self._s, ci_ids)
        flat = [v for versions in by_ci.values() for v in versions]
        xml = await reader.version_xml_data(self._s, flat)
        return {
            ci_id: [
                ZnunyCiVersion(
                    id=v.id,
                    ci_id=v.ci_id,
                    name=v.name,
                    definition_id=v.definition_id,
                    deployment_state_id=v.depl_state_id,
                    incident_state_id=v.inci_state_id,
                    created_at=v.created_at,
                    created_by=_author(v.created_by),
                    xml_data=_xml_node(xml.get(v.id, [])),
                )
                for v in versions
            ]
            for ci_id, versions in by_ci.items()
        }

    async def links_for_cis(self, ci_ids: Sequence[int]) -> list[ZnunyLink]:
        reader = await self._reader()
        return await source_extra.links_for_cis(
            self._s, ci_ids, ci_object=reader.profile.ci_link_object
        )

    async def attachments(self, ci_ids: Sequence[int], *, with_data: bool) -> list[ZnunyAttachment]:
        await self._reader()
        return await source_extra.read_ci_attachments(self._s, ci_ids, with_data=with_data)

    async def change_counts(self) -> dict[str, int]:
        return await source_extra.count_changes(self._s)

    async def cip_rows(self) -> list[CipRow]:
        reader = await self._core_reader()
        return await reader.cip_allocate(self._s)

    async def service_itsm_rows(self) -> list[ServiceItsmRow]:
        reader = await self._core_reader()
        return list((await reader.service_itsm_columns(self._s)).values())

    async def max_ci_change_time(self) -> datetime | None:
        await self._reader()
        return await source_extra.max_ci_change_time(self._s)

    async def count_cis_changed_since(self, since: datetime) -> int:
        await self._reader()
        return await source_extra.count_cis_changed_since(self._s, since)


class CmdbServiceWriter:
    """:class:`CmdbWritePort`: one-line delegations to ``tiqora.cmdb.importer_api``."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def upsert_catalog_item(
        self,
        *,
        catalog_class: str,
        item_key: str,
        name: str,
        functionality: str | None,
        valid: bool,
        position: int = 0,
    ) -> int:
        return await importer_api.upsert_catalog_item(
            self._s,
            catalog_class=catalog_class,
            item_key=item_key,
            name=name,
            functionality=functionality,
            valid=valid,
            position=position,
        )

    async def upsert_class(
        self,
        *,
        class_key: str,
        name: str,
        group_id: int,
        number_prefix: str,
        user_id: int,
        icon: str | None = None,
        portal_visible: bool = False,
    ) -> int:
        return await importer_api.upsert_class(
            self._s,
            class_key=class_key,
            name=name,
            group_id=group_id,
            number_prefix=number_prefix,
            user_id=user_id,
            icon=icon,
            portal_visible=portal_visible,
        )

    async def add_definition(
        self,
        *,
        class_id: int,
        attributes: Sequence[Any],
        comment: str | None,
        created_by: int,
        created_at: datetime,
        make_current: bool = True,
        skip_if_equal: bool = True,
        extra_class_keys: frozenset[str] = frozenset(),
    ) -> int:
        return await importer_api.add_definition(
            self._s,
            class_id=class_id,
            attributes=list(attributes),
            comment=comment,
            created_by=created_by,
            created_at=created_at,
            make_current=make_current,
            skip_if_equal=skip_if_equal,
            extra_class_keys=extra_class_keys,
        )

    async def create_imported_ci(
        self,
        *,
        class_id: int,
        number: str,
        name: str,
        created_by: int,
        created_at: datetime,
        deployment_state_id: int | None = None,
        incident_state_id: int | None = None,
    ) -> int:
        return await importer_api.create_imported_ci(
            self._s,
            class_id=class_id,
            number=number,
            name=name,
            created_by=created_by,
            created_at=created_at,
            deployment_state_id=deployment_state_id,
            incident_state_id=incident_state_id,
        )

    async def add_imported_version(
        self,
        ci_id: int,
        *,
        definition_id: int,
        name: str,
        deployment_state_id: int,
        incident_state_id: int,
        attributes: dict[str, Any],
        created_by: int,
        created_at: datetime,
        change_note: str | None = None,
        write_index: bool = True,
    ) -> int | None:
        return await importer_api.add_imported_version(
            self._s,
            ci_id,
            importer_api.ImportedVersion(
                definition_id=definition_id,
                name=name,
                deployment_state_id=deployment_state_id,
                incident_state_id=incident_state_id,
                attributes=attributes,
                created_by=created_by,
                created_at=created_at,
                change_note=change_note,
            ),
            write_index=write_index,
        )

    async def latest_version_id(self, ci_id: int) -> int | None:
        return await importer_api.latest_version_id(self._s, ci_id)

    async def ensure_link_types(self, *, user_id: int) -> int:
        return await sync_link_type_rows(self._s, user_id=user_id)

    async def add_link(
        self,
        *,
        source_kind: str,
        source_key: str,
        target_kind: str,
        target_key: str,
        link_type: str,
        created_by: int,
        created_at: datetime,
    ) -> bool:
        return await importer_api.import_link(
            self._s,
            source_kind=source_kind,
            source_key=source_key,
            target_kind=target_kind,
            target_key=target_key,
            link_type_key=link_type,
            created_by=created_by,
            created_at=created_at,
        )

    async def add_imported_attachment(
        self,
        ci_id: int,
        *,
        filename: str,
        content_type: str,
        data: bytes,
        created_at: datetime,
        created_by: int,
    ) -> int:
        return await importer_api.add_imported_attachment(
            self._s,
            ci_id,
            filename=filename,
            content_type=content_type,
            data=data,
            created_at=created_at,
            created_by=created_by,
        )

    async def import_history(
        self, ci_id: int, rows: Sequence[tuple[str, str, int, datetime]]
    ) -> int:
        return await importer_api.import_history(self._s, ci_id, rows)

    async def delete_ci(self, ci_id: int, *, user_id: int) -> None:
        await importer_api.delete_ci(self._s, ci_id, user_id=user_id)

    async def finish_import(self, ci_ids: Sequence[int]) -> int:
        return (await importer_api.finish_import(self._s, ci_ids)).updated


def _pattern(field: str, raw: str) -> Condition:
    """Znuny ``*`` wildcards: none = exact, trailing = prefix, leading = contains."""
    value = raw.strip()
    lead, trail = value.startswith("*"), value.endswith("*")
    core = value.strip("*")
    if lead:
        return Condition(field=field, op="contains", value=core)
    if trail:
        return Condition(field=field, op="starts_with", value=core)
    return Condition(field=field, op="eq", value=core)


class CmdbServiceReader:
    """:class:`CmdbReadPort` on the cmdb services (permission checks are the caller's job
    through :meth:`visible_class_ids`; ``get_ci`` and ``search_cis`` do not enforce them)."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def visible_class_ids(self, user_id: int, perm: Literal["ro", "rw"]) -> set[int]:
        access = await load_access(self._s, user_id)
        ids = (
            await readable_class_ids(self._s, access)
            if perm == "ro"
            else await writable_class_ids(self._s, access)
        )
        return set(ids)

    async def get_ci(self, ci_id: int, *, version_id: int | None = None) -> CiView | None:
        svc = CiService(self._s)
        access = system_access(_ROOT_USER)
        try:
            rec = await svc.get(access, ci_id, enforce_permissions=False)
            classes = ClassService(self._s)
            cls = await classes.get(rec.class_id)
            if version_id is None:
                definition_id = rec.definition_id
                name, attributes = rec.name, rec.attributes
                dep_id, inc_id = rec.deployment_state_id, rec.incident_state_id
                vid = rec.current_version_id
                cur_inc_id = rec.cur_incident_state_id
                changed_by: int | None = rec.changed_by
                if definition_id is None:
                    def_version, attrs = 0, []
                else:
                    d = await classes.definition(definition_id)
                    def_version, attrs = d.version, d.attributes
            else:
                v = await svc.get_version(access, ci_id, version_id, enforce_permissions=False)
                name, attributes = v.name, v.attributes
                dep_id, inc_id = v.deployment_state_id, v.incident_state_id
                vid, def_version, attrs = v.id, v.definition_version, v.definition_attrs
                definition_id, cur_inc_id, changed_by = v.definition_id, inc_id, v.created_by
        except CmdbNotFound:
            return None
        info = await self._catalog_info({dep_id, inc_id, cur_inc_id})
        attachments = (
            await self._s.execute(
                select(
                    TiqoraCmdbAttachment.id,
                    TiqoraCmdbAttachment.filename,
                    TiqoraCmdbAttachment.content_type,
                    TiqoraCmdbAttachment.size_bytes,
                )
                .where(TiqoraCmdbAttachment.ci_id == ci_id)
                .order_by(TiqoraCmdbAttachment.id)
            )
        ).all()
        return CiView(
            id=rec.id,
            number=rec.number,
            name=name,
            class_id=rec.class_id,
            class_key=cls.class_key,
            class_name=cls.name,
            deployment_state=info.get(dep_id, ("", None))[0],
            deployment_state_id=dep_id,
            incident_state=info.get(inc_id, ("", None))[0],
            incident_state_id=inc_id,
            version_id=vid,
            definition_version=def_version,
            attributes=attributes,
            schema=_schema(attrs),
            created_at=rec.created_at,
            created_by=rec.created_by,
            changed_at=rec.changed_at,
            attachments=[(int(a), str(f), str(c), int(s)) for a, f, c, s in attachments],
            changed_by=changed_by,
            definition_id=definition_id,
            deployment_functionality=info.get(dep_id, ("", None))[1],
            incident_functionality=info.get(inc_id, ("", None))[1],
            cur_deployment_state=info.get(dep_id, ("", None))[0],
            cur_deployment_functionality=info.get(dep_id, ("", None))[1],
            cur_incident_state=info.get(cur_inc_id, ("", None))[0],
            cur_incident_functionality=info.get(cur_inc_id, ("", None))[1],
            last_version_id=rec.current_version_id,
        )

    async def _catalog_info(self, ids: set[int]) -> dict[int, tuple[str, str | None]]:
        """Catalog id -> (name, functionality)."""
        rows = await self._s.execute(
            select(
                TiqoraCmdbCatalog.id, TiqoraCmdbCatalog.name, TiqoraCmdbCatalog.functionality
            ).where(TiqoraCmdbCatalog.id.in_(sorted(ids)))
        )
        return {int(i): (str(n), f) for i, n, f in rows.all()}

    async def ci_id_by_number(self, number: str) -> int | None:
        found = (
            await self._s.execute(select(TiqoraCmdbCi.id).where(TiqoraCmdbCi.number == number))
        ).scalar_one_or_none()
        return None if found is None else int(found)

    async def search_cis(
        self, query: CiQuery, *, class_ids: set[int], limit: int, offset: int
    ) -> list[int]:
        """CI ids matching ``query`` inside ``class_ids``. Attribute filters need exactly one
        class (``FilterError`` otherwise); an unknown state name matches nothing."""
        keys = await self._class_keys(class_ids)
        if query.class_keys:
            keys = [k for k in keys if k in set(query.class_keys)]
        if not keys:
            return []
        conds: list[FilterNode] = [Condition(field="class", op="in", value=keys)]
        if query.number:
            conds.append(_pattern("number", query.number))
        if query.name:
            conds.append(_pattern("name", query.name))
        for field, catalog_class, names in (
            ("deployment_state", DEPLOYMENT_STATE, query.deployment_states),
            ("incident_state", INCIDENT_STATE, query.incident_states),
        ):
            if names:
                catalog = CatalogService(self._s)
                ids = [await catalog.resolve_name(catalog_class, n) for n in names]
                found = [str(i) for i in ids if i is not None]
                if not found:
                    return []
                conds.append(Condition(field=field, op="in", value=found))
        for field, op, when in (
            ("created_at", "after", query.created_after),
            ("created_at", "before", query.created_before),
            ("changed_at", "after", query.changed_after),
            ("changed_at", "before", query.changed_before),
        ):
            if when is not None:
                conds.append(Condition(field=field, op=op, value=when.isoformat()))  # type: ignore[arg-type]
        scope: list[int] = []
        for path, raw in (query.attributes or {}).items():
            conds.append(_pattern(f"attr.{path}", raw))
            scope = await self._ids_of(keys)
        flt = Filter(where=AllOf(all=conds))
        access = system_access(_ROOT_USER)
        out: list[int] = []
        skip, want = max(0, offset), max(0, limit)
        pos = 0
        while len(out) < want:
            body = CiSearchIn(
                class_ids=scope,
                filter=flt,
                q=query.text or None,
                offset=pos,
                limit=_SEARCH_PAGE,
            )
            page = await search_cis(self._s, access, body)
            for item in page.items:
                if skip > 0:
                    skip -= 1
                else:
                    out.append(item.id)
                    if len(out) >= want:
                        break
            if len(page.items) < _SEARCH_PAGE:
                break
            pos += _SEARCH_PAGE
        return out

    async def _class_keys(self, class_ids: set[int]) -> list[str]:
        if not class_ids:
            return []
        rows = await self._s.execute(
            select(TiqoraCmdbClass.class_key)
            .where(TiqoraCmdbClass.id.in_(sorted(class_ids)))
            .order_by(TiqoraCmdbClass.id)
        )
        return [str(k) for k in rows.scalars().all()]

    async def _ids_of(self, keys: list[str]) -> list[int]:
        rows = await self._s.execute(
            select(TiqoraCmdbClass.id).where(TiqoraCmdbClass.class_key.in_(keys))
        )
        return [int(i) for i in rows.scalars().all()]

    async def class_by_key_or_name(self, value: str) -> CmdbClassView | None:
        svc = ClassService(self._s)
        needle = value.strip()
        rec = None
        try:
            rec = await svc.get_by_key(needle)
        except CmdbNotFound:
            for candidate in await svc.list(include_invalid=True):
                if candidate.name.casefold() == needle.casefold():
                    rec = candidate
                    break
        if rec is None:
            return None
        if rec.current_definition_id is None:
            return CmdbClassView(rec.id, rec.class_key, rec.name, rec.group_id, _schema([]), 0)
        d = await svc.definition(rec.current_definition_id)
        return CmdbClassView(
            rec.id, rec.class_key, rec.name, rec.group_id, _schema(d.attributes), d.version
        )

    async def catalog_item(self, catalog_class: str, name_or_id: str | int) -> CatalogView | None:
        needle = str(name_or_id).strip().casefold()
        for item in await CatalogService(self._s).items(catalog_class, include_invalid=True):
            if (
                str(item.id) == needle
                or item.name.casefold() == needle
                or (item.item_key.casefold() == needle)
            ):
                return CatalogView(
                    item.id, item.catalog_class, item.item_key, item.name, item.functionality
                )
        return None

    async def impact(self, ci_id: int, *, max_depth: int, user_id: int) -> ImpactView:
        """CIs that depend on this one (``up``, ``max_depth`` clamped by cmdb to 1-5) plus the
        services, customers and open tickets an incident would reach (readable classes and
        queues only). ``CmdbNotFound`` / ``CmdbForbidden`` propagate."""
        access = await load_access(self._s, user_id)
        settings = await load_cmdb_settings(self._s)
        graph = await impact_graph(
            self._s, access, ci_id, depth=max_depth, direction="up", settings=settings
        )
        reach = await affected(self._s, access, ci_id, settings=settings)
        return ImpactView(
            cis=[
                (n.ci_id, n.number or "", n.name or "")
                for n in graph.nodes
                if n.kind == "ci" and not n.hidden and n.ci_id is not None
            ],
            services=[(int(s.id), s.label) for s in reach.services if s.id.isdigit()],
            customers=[(c.id, c.label) for c in reach.customers],
            open_tickets=[(t.ticket_id, t.number, t.title) for t in reach.open_tickets],
            truncated=graph.truncated or reach.truncated,
        )


def _schema(attrs: Sequence[AttributeDef]) -> dict[str, Any]:
    return {"attributes": json.loads(dump_definition_json(list(attrs)))}


class ItsmModeFacade:
    """:class:`ItsmModePort` on itsm1's ``mode`` module and the Znuny detection."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def effective_mode(self) -> ItsmModeName:
        return (await itsm_mode.get_itsm_mode(self._s, fresh=True)).mode

    async def znuny_itsm_present(self) -> bool:
        if znuny_present_override is not None:
            return znuny_present_override
        return (await detect_znuny_itsm(self._s, use_cache=False)).core_installed

    async def is_switched(self) -> bool:
        return await itsm_mode.is_switched(self._s)

    async def switch_to_tiqora(self, *, user_id: int, import_run_id: int) -> ItsmModeName:
        state = await itsm_mode.switch_to_tiqora(
            self._s, user_id=user_id, import_run_id=import_run_id
        )
        return state.mode

    async def rollback_to_znuny(self, *, user_id: int, import_run_id: int) -> ItsmModeName:
        state = await itsm_mode.rollback_to_znuny(
            self._s, user_id=user_id, import_run_id=import_run_id
        )
        return state.mode


# --- itsm1 write functions of the delegated steps (Task 12) ------------------------------------


def _clip(value: str | None) -> str | None:
    return value[:200] if value else None


async def import_priority_matrix(
    session: AsyncSession, rows: Sequence[CipRow], *, user_id: int
) -> int:
    """Replace Tiqora's matrix by Znuny's ``cip_allocate`` (itsm1 ``replace_matrix_from_import``).

    Raises :class:`InvalidMatrix` before anything is written. Flush only; returns the cell count.
    """
    cells = [MatrixCell(r.criticality, r.impact, r.priority_id) for r in rows]
    await replace_matrix_from_import(session, cells, user_id=user_id)
    return len(cells)


async def import_service_meta(session: AsyncSession, row: ServiceItsmRow, *, user_id: int) -> None:
    """Store the ITSM columns of one Znuny service as Tiqora's type and criticality.

    Owner, description, portal flag and incident window of an existing meta row stay as they are
    (``save_service_meta`` replaces the row). Raises :class:`ServiceNotFound`. Flush only.
    """
    current = await session.get(TiqoraItsmServiceMeta, row.service_id)
    data = ServiceMetaIn(
        service_type=_clip(row.type_name),
        criticality=_clip(row.criticality),
        owner_user_id=None if current is None else current.owner_user_id,
        description=None if current is None else current.description,
        portal_visible=True if current is None else bool(current.portal_visible),
        min_minutes_between_incidents=None
        if current is None
        else current.min_minutes_between_incidents,
    )
    await save_service_meta(session, row.service_id, data, user_id=user_id, import_mode=True)


# --- GI reads (Task 21) -----------------------------------------------------------------------


async def cmdb_attachment_contents(
    session: AsyncSession, ci_id: int, attachment_ids: Sequence[int]
) -> list[tuple[str, str, bytes]]:
    """(filename, content type, bytes) of the given attachments of a Tiqora CI, in the order
    given. Used by GI ``ConfigItemGet`` after the caller proved class permission."""
    if not attachment_ids:
        return []
    rows = (
        await session.execute(
            select(
                TiqoraCmdbAttachment.id,
                TiqoraCmdbAttachment.filename,
                TiqoraCmdbAttachment.content_type,
                TiqoraCmdbAttachment.content,
            ).where(
                TiqoraCmdbAttachment.ci_id == ci_id,
                TiqoraCmdbAttachment.id.in_(list(attachment_ids)),
            )
        )
    ).all()
    by_id = {int(i): (str(f), str(c), bytes(b)) for i, f, c, b in rows}
    return [by_id[i] for i in attachment_ids if i in by_id]


class _IdentityResolver:
    """Mode A keeps Znuny ids: catalog and CI references stay what Znuny stored."""

    def ci(self, znuny_ci_id: str) -> int | None:
        return int(znuny_ci_id) if znuny_ci_id.isdigit() else None

    def catalog(self, znuny_gc_id: str) -> int | None:
        return int(znuny_gc_id) if znuny_gc_id.isdigit() else None


class ModeAReader:
    """Znuny's own CMDB as a :class:`CiView` (GI ``ConfigItemGet`` in Mode A).

    The class-group permission is Znuny's ``ItemClassGroupCheck``: the user holds ``ro`` (``rw``
    counts) on the group in the class's ``Permission`` preference. Ids in the view (catalog,
    referenced CIs) are Znuny ids. Attachment bytes come from ``virtual_fs``.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._s = session
        self._source: ImportSource = (
            source_override if source_override is not None else ZnunyAdapterSource(session)
        )

    async def get_ci(self, znuny_id: int, *, user_id: int) -> CiView | None:
        """``None`` for an unknown CI, a class without ``ro`` for ``user_id`` or no version."""
        source = self._source
        cis = await source.cis([znuny_id])
        if not cis:
            return None
        ci = cis[0]
        catalog = {c.id: c for c in await source.catalog_items()}
        cls = catalog.get(ci.class_id)
        if cls is None or cls.permission_group is None:
            return None
        from tiqora.permissions.engine import PermissionEngine

        if cls.permission_group not in await PermissionEngine(self._s).groups_for_permission(
            user_id, "ro"
        ):
            return None
        versions = (await source.versions([znuny_id])).get(znuny_id) or []
        if not versions:
            return None
        current = next((v for v in versions if v.id == ci.last_version_id), versions[-1])
        definition = next(
            (d for d in await source.class_definitions() if d.id == current.definition_id), None
        )
        if definition is None:
            return None
        parsed = parse_definition_with_issues(definition.raw, definition_format(definition.raw))
        schema = _schema(parsed.result.attributes)
        attributes, _issues = xmldata_to_attributes(current.xml_data, schema, _IdentityResolver())

        def state(state_id: int | None) -> tuple[str, str | None]:
            item = catalog.get(state_id or 0)
            return (item.name, item.functionality) if item is not None else ("", None)

        dep, inc = state(current.deployment_state_id), state(current.incident_state_id)
        cur_dep, cur_inc = state(ci.deployment_state_id), state(ci.incident_state_id)
        attachments = await source.attachments([znuny_id], with_data=False)
        return CiView(
            id=ci.id,
            number=ci.number,
            name=current.name,
            class_id=ci.class_id,
            class_key=str(ci.class_id),
            class_name=cls.name,
            deployment_state=dep[0],
            deployment_state_id=current.deployment_state_id or 0,
            incident_state=inc[0],
            incident_state_id=current.incident_state_id or 0,
            version_id=current.id,
            definition_version=definition.version,
            attributes=attributes,
            schema=schema,
            created_at=ci.created_at,
            created_by=ci.created_by,
            changed_at=current.created_at,
            attachments=[(0, a.filename, a.content_type, a.size) for a in attachments],
            changed_by=current.created_by,
            definition_id=current.definition_id,
            deployment_functionality=dep[1],
            incident_functionality=inc[1],
            cur_deployment_state=cur_dep[0],
            cur_deployment_functionality=cur_dep[1],
            cur_incident_state=cur_inc[0],
            cur_incident_functionality=cur_inc[1],
            last_version_id=ci.last_version_id,
        )

    async def attachment_contents(self, znuny_id: int) -> list[tuple[str, str, bytes]]:
        found = await self._source.attachments([znuny_id], with_data=True)
        return [(a.filename, a.content_type, a.data or b"") for a in found]

    async def catalog_names(self) -> dict[int, str]:
        """Znuny general-catalog id -> name (``GeneralCatalog`` attribute values)."""
        return {c.id: c.name for c in await self._source.catalog_items()}


def get_mode_a_reader(session: AsyncSession) -> ModeAReader:
    return mode_a_override if mode_a_override is not None else ModeAReader(session)


def get_ports(session: AsyncSession) -> Ports:
    """All four ports bound to ``session`` (the caller owns the transaction)."""
    source: ImportSource = (
        source_override if source_override is not None else ZnunyAdapterSource(session)
    )
    writer: CmdbWritePort = CmdbServiceWriter(session)
    reader: CmdbReadPort = CmdbServiceReader(session)
    mode: ItsmModePort = ItsmModeFacade(session)
    return Ports(source=source, writer=writer, reader=reader, mode=mode)
