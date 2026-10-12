"""Mode-B CI provider: the Tiqora CMDB behind itsm1's ``CiProvider`` contract (design I3).

The contract types come from ``tiqora.itsm.ci_provider`` (step 0, owner itsm1); this module only
implements them (cross ruling R3) and is registered in ``register_core`` as
``CiProviderFactory(mode="tiqora", ...)``. Rules it follows:

* ``source = "tiqora"``, ``read_only = False``; a ref of the other source is :class:`CiNotFound`
  (``get``, writes) or silently omitted (``get_many``);
* no method commits or rolls back (``LinkService.add`` opens a SAVEPOINT for the unique-key race,
  which is not a transaction end);
* outside ITSM mode ``tiqora`` every read answers empty (``is_empty`` is never gated, it decides
  whether the mode may be switched) and every write raises :class:`CiUnavailable`;
* ``search`` counts ``total`` exactly and clamps ``limit`` to 100; no ``tiqora_*`` x Znuny JOIN,
  Znuny rows (ticket queues) are read by IN list.

There is deliberately no ``impact_summary``: the CMDB's "Auswirkungen" stay on
``/api/v1/cmdb/cis/{id}/affected``.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Iterable, Sequence
from datetime import datetime
from typing import Any, Final, Literal

from sqlalchemy import and_, bindparam, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.cmdb.access import CmdbAccess, load_access, readable_class_ids, writable_class_ids
from tiqora.cmdb.catalog import CatalogService
from tiqora.cmdb.errors import CmdbForbidden, CmdbNotFound
from tiqora.cmdb.gate import CmdbUnavailable, current_itsm_mode
from tiqora.cmdb.impact import _ticket_links
from tiqora.cmdb.links import LinkEnd, LinkInvalid, LinkService, LinkTypeService, UnknownLinkType
from tiqora.cmdb.propagation import service_incident_states as _service_incident_states
from tiqora.cmdb.render import RenderedAttr, render_values
from tiqora.cmdb.schemas import CiSearchCriteria
from tiqora.cmdb.search import ci_search_statement
from tiqora.cmdb.seed import DEPLOYMENT_STATE, INCIDENT_STATE
from tiqora.cmdb.service import CiService
from tiqora.db.tiqora.models import (
    TiqoraCmdbCatalog,
    TiqoraCmdbCi,
    TiqoraCmdbCiVersion,
    TiqoraCmdbCiVersionArchive,
    TiqoraCmdbClass,
    TiqoraItsmLink,
)
from tiqora.filters.catalog import FilterError
from tiqora.itsm.ci_provider import (
    CatalogValue,
    CiAttribute,
    CiClassInfo,
    CiDetail,
    CiError,
    CiNotFound,
    CiPage,
    CiPermissionDenied,
    CiProvider,
    CiQuery,
    CiRef,
    CiSource,
    CiSummary,
    CiTicketLink,
    CiUnavailable,
    CiVersionInfo,
    LinkTypeOption,
    ServiceIncidentState,
)
from tiqora.itsm.models import TiqoraItsmLinkType
from tiqora.itsm.providers import CiProviderFactory
from tiqora.permissions.engine import PermissionEngine
from tiqora.znuny.sysconfig import SysConfig

SOURCE: Final = "tiqora"
_LIMIT_MAX: Final = 100
_CHUNK: Final = 500
_DEFAULT_LINK_TYPE: Final = "RelevantTo"
_VERSION_SCAN_MAX: Final = 10000

#: ``AttributeDef.type`` -> contract ``CiAttribute.kind``
_KIND: Final[dict[str, str]] = {
    "text": "text",
    "textarea": "textarea",
    "integer": "integer",
    "decimal": "other",
    "date": "date",
    "datetime": "datetime",
    "catalog": "catalog",
    "ci_ref": "ci",
    "customer_user": "customer",
    "customer_company": "customer_company",
    "agent": "user",
    "service": "service",
    "sla": "sla",
    "queue": "queue",
    "priority": "priority",
    "ticket_type": "ticket_type",
    "state": "state",
    "url": "url",
    "email": "email",
    "ip": "ip",
}
_SEGMENT = re.compile(r"([A-Za-z][A-Za-z0-9_]*)\[(\d+)\]")


def _chunks[T](items: Sequence[T], size: int = _CHUNK) -> Iterable[Sequence[T]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _own(refs: Iterable[CiRef]) -> list[int]:
    return sorted({r.id for r in refs if r.source == SOURCE})


def _ref(ci_id: int) -> CiRef:
    return CiRef(SOURCE, int(ci_id))


def _gate_error(exc: CmdbUnavailable) -> CiUnavailable:
    return CiUnavailable("cmdb_not_installed", f"itsm mode {exc.mode}")


async def _active(session: AsyncSession) -> bool:
    return await current_itsm_mode(session) == "tiqora"


async def _guarded[T](call: Awaitable[T]) -> T:
    """Run a CMDB service call; translate its domain errors into the contract's."""
    try:
        return await call
    except CmdbNotFound as exc:
        raise CiNotFound(str(exc)) from exc
    except CmdbForbidden as exc:
        raise CiPermissionDenied(str(exc)) from exc
    except CmdbUnavailable as exc:
        raise _gate_error(exc) from exc
    except (UnknownLinkType, LinkInvalid, FilterError) as exc:
        raise CiError(str(exc)) from exc


async def _catalog_values(session: AsyncSession, ids: Iterable[int]) -> dict[int, CatalogValue]:
    wanted = sorted({int(i) for i in ids})
    out: dict[int, CatalogValue] = {}
    for chunk in _chunks(wanted):
        rows = await session.execute(
            select(
                TiqoraCmdbCatalog.id,
                TiqoraCmdbCatalog.item_key,
                TiqoraCmdbCatalog.name,
                TiqoraCmdbCatalog.functionality,
            ).where(TiqoraCmdbCatalog.id.in_(list(chunk)))
        )
        out.update({int(i): CatalogValue(str(k), str(n), f) for i, k, n, f in rows.all()})
    return out


async def _summaries(
    session: AsyncSession, access: CmdbAccess, ci_ids: Iterable[int]
) -> dict[int, CiSummary]:
    """Summaries of the readable CIs among ``ci_ids`` (unknown and unreadable ones omitted)."""
    wanted = sorted({int(i) for i in ci_ids})
    if not wanted:
        return {}
    readable = await readable_class_ids(session, access)
    if not readable:
        return {}
    rows: list[Any] = []
    for chunk in _chunks(wanted):
        res = await session.execute(
            select(
                TiqoraCmdbCi.id,
                TiqoraCmdbCi.number,
                TiqoraCmdbCi.name,
                TiqoraCmdbCi.deployment_state_id,
                TiqoraCmdbCi.cur_incident_state_id,
                TiqoraCmdbCi.changed_at,
                TiqoraCmdbClass.class_key,
                TiqoraCmdbClass.name.label("class_name"),
            )
            .join(TiqoraCmdbClass, TiqoraCmdbClass.id == TiqoraCmdbCi.class_id)
            .where(TiqoraCmdbCi.id.in_(list(chunk)), TiqoraCmdbCi.class_id.in_(readable))
        )
        rows.extend(res.all())
    values = await _catalog_values(
        session,
        [r.deployment_state_id for r in rows] + [r.cur_incident_state_id for r in rows],
    )
    return {
        int(r.id): CiSummary(
            ref=_ref(r.id),
            number=str(r.number),
            name=str(r.name),
            class_key=str(r.class_key),
            class_name=str(r.class_name),
            deployment_state=values.get(int(r.deployment_state_id)),
            incident_state=values.get(int(r.cur_incident_state_id)),
            changed_at=r.changed_at,
            edit_url=None,
        )
        for r in rows
    }


def _raw(values: Any, path: str) -> str | None:
    """The stored value at a positional path such as ``NIC[1].IPAddress[2]``."""
    node: Any = values
    segments = path.split(".")
    for pos, segment in enumerate(segments):
        m = _SEGMENT.fullmatch(segment)
        if m is None or not isinstance(node, dict):
            return None
        entries = node.get(m.group(1))
        idx = int(m.group(2)) - 1
        if not isinstance(entries, list) or not 0 <= idx < len(entries):
            return None
        entry = entries[idx]
        if not isinstance(entry, dict):
            return None
        if pos == len(segments) - 1:
            value = entry.get("v")
            return None if value is None else str(value)
        node = entry.get("s")
    return None


def _attribute(node: RenderedAttr, values: Any) -> CiAttribute:
    ref: CiRef | None = None
    if node.ref is not None and node.ref.kind == "ci" and node.ref.key.isdigit():
        ref = _ref(int(node.ref.key))
    return CiAttribute(
        key=node.key,
        label=node.label,
        kind=_KIND.get(node.type, "other"),
        display=node.display or "",
        raw=_raw(values, node.path),
        ref=ref,
        children=tuple(_attribute(c, values) for c in node.children),
    )


class TiqoraCiProvider:
    """``CiProvider`` of ITSM mode ``tiqora``."""

    source: CiSource = "tiqora"
    read_only: bool = False

    # --- emptiness, catalogs, classes ------------------------------------------------------

    async def is_empty(self, session: AsyncSession) -> bool:
        found = (await session.execute(select(TiqoraCmdbCi.id).limit(1))).first()
        return found is None

    async def classes(self, session: AsyncSession, *, user_id: int) -> list[CiClassInfo]:
        if not await _active(session):
            return []
        access = await load_access(session, user_id)
        readable = set(await readable_class_ids(session, access))
        writable = set(await writable_class_ids(session, access))
        if not readable:
            return []
        rows = (
            await session.execute(
                select(TiqoraCmdbClass)
                .where(TiqoraCmdbClass.id.in_(sorted(readable)), TiqoraCmdbClass.valid.is_(True))
                .order_by(TiqoraCmdbClass.position, TiqoraCmdbClass.name, TiqoraCmdbClass.id)
            )
        ).scalars()
        return [
            CiClassInfo(
                key=c.class_key,
                name=c.name,
                icon=c.icon,
                can_read=True,
                can_write=int(c.id) in writable,
            )
            for c in rows
        ]

    async def _states(self, session: AsyncSession, catalog_class: str) -> list[CatalogValue]:
        if not await _active(session):
            return []
        return [
            CatalogValue(i.item_key, i.name, i.functionality)
            for i in await CatalogService(session).items(catalog_class)
        ]

    async def deployment_states(self, session: AsyncSession) -> list[CatalogValue]:
        return await self._states(session, DEPLOYMENT_STATE)

    async def incident_states(self, session: AsyncSession) -> list[CatalogValue]:
        return await self._states(session, INCIDENT_STATE)

    # --- search, get -----------------------------------------------------------------------

    async def search(self, session: AsyncSession, query: CiQuery, *, user_id: int) -> CiPage:
        if not await _active(session):
            return CiPage(items=(), total=0)
        limit = max(1, min(int(query.limit), _LIMIT_MAX))
        offset = max(0, int(query.offset))
        access = await load_access(session, user_id)
        empty = CiPage(items=(), total=0)

        class_ids: list[int] = []
        if query.class_keys:
            class_ids = [
                int(i)
                for i in (
                    await session.execute(
                        select(TiqoraCmdbClass.id).where(
                            TiqoraCmdbClass.class_key.in_(list(query.class_keys))
                        )
                    )
                )
                .scalars()
                .all()
            ]
            if not class_ids:
                return empty
        text_q = (query.text or "").strip()[:200] or None
        stmt = await _guarded(
            ci_search_statement(session, access, CiSearchCriteria(class_ids=class_ids, q=text_q))
        )
        if stmt is None:
            return empty
        for keys, catalog_class, column in (
            (query.deployment_state_keys, DEPLOYMENT_STATE, TiqoraCmdbCi.deployment_state_id),
            (query.incident_state_keys, INCIDENT_STATE, TiqoraCmdbCi.cur_incident_state_id),
        ):
            if not keys:
                continue
            state_ids = (
                (
                    await session.execute(
                        select(TiqoraCmdbCatalog.id).where(
                            TiqoraCmdbCatalog.catalog_class == catalog_class,
                            TiqoraCmdbCatalog.item_key.in_(list(keys)),
                        )
                    )
                )
                .scalars()
                .all()
            )
            if not state_ids:
                return empty
            stmt = stmt.where(column.in_([int(i) for i in state_ids]))
        if query.refs:
            own = _own(query.refs)
            if not own:
                return empty
            stmt = stmt.where(TiqoraCmdbCi.id.in_(own))
        total = int(
            (
                await session.execute(
                    select(func.count()).select_from(
                        stmt.with_only_columns(TiqoraCmdbCi.id, maintain_column_froms=True)
                        .order_by(None)
                        .subquery()
                    )
                )
            ).scalar_one()
        )
        ids = [
            int(i)
            for i in (
                await session.execute(
                    stmt.with_only_columns(TiqoraCmdbCi.id, maintain_column_froms=True)
                    .order_by(TiqoraCmdbCi.number.asc(), TiqoraCmdbCi.id.asc())
                    .offset(offset)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        ]
        summaries = await _summaries(session, access, ids)
        return CiPage(items=tuple(summaries[i] for i in ids if i in summaries), total=total)

    async def get(
        self,
        session: AsyncSession,
        ref: CiRef,
        *,
        user_id: int,
        version_id: int | None = None,
    ) -> CiDetail:
        if ref.source != SOURCE:
            raise CiNotFound(str(ref))
        if not await _active(session):
            raise CiNotFound(str(ref))
        access = await load_access(session, user_id)
        service = CiService(session)
        rec = await _guarded(service.get(access, ref.id))
        summary = (await _summaries(session, access, [ref.id])).get(ref.id)
        if summary is None:
            raise CiNotFound(str(ref))
        shown_id = version_id if version_id is not None else rec.current_version_id
        version_info: CiVersionInfo | None = None
        attributes: tuple[CiAttribute, ...] = ()
        definition_version: int | None = None
        if shown_id is not None:
            version = await _guarded(service.get_version(access, ref.id, shown_id))
            definition_version = version.definition_version
            rendered = await render_values(
                session, version.definition_attrs, version.attributes, access=access
            )
            attributes = tuple(_attribute(n, version.attributes) for n in rendered)
            version_info = (await self._version_infos(session, ref.id, [version.id]))[0]
        return CiDetail(
            summary=summary,
            version=version_info,
            attributes=attributes,
            definition_version=definition_version,
            read_only=False,
        )

    async def get_many(
        self, session: AsyncSession, refs: Sequence[CiRef], *, user_id: int
    ) -> dict[CiRef, CiSummary]:
        own = _own(refs)
        if not own or not await _active(session):
            return {}
        access = await load_access(session, user_id)
        return {s.ref: s for s in (await _summaries(session, access, own)).values()}

    # --- versions --------------------------------------------------------------------------

    async def _version_infos(
        self, session: AsyncSession, ci_id: int, version_ids: Sequence[int]
    ) -> list[CiVersionInfo]:
        """Contract rows for ``version_ids`` (hot table first, then the archive)."""
        rows: dict[int, Any] = {}
        for model in (TiqoraCmdbCiVersion, TiqoraCmdbCiVersionArchive):
            res = await session.execute(
                select(
                    model.id,
                    model.name,
                    model.deployment_state_id,
                    model.incident_state_id,
                    model.change_note,
                    model.created_by,
                    model.created_at,
                ).where(model.ci_id == ci_id, model.id.in_(list(version_ids)))
            )
            for r in res.all():
                rows.setdefault(int(r.id), r)
        values = await _catalog_values(
            session,
            [r.deployment_state_id for r in rows.values()]
            + [r.incident_state_id for r in rows.values()],
        )
        users = await _user_names(session, {int(r.created_by) for r in rows.values()})
        # seq = position among all versions of the CI, oldest = 1
        out: list[CiVersionInfo] = []
        for vid in version_ids:
            row = rows.get(vid)
            if row is None:
                continue
            seq = 0
            for model in (TiqoraCmdbCiVersion, TiqoraCmdbCiVersionArchive):
                seq += int(
                    (
                        await session.execute(
                            select(func.count())
                            .select_from(model)
                            .where(model.ci_id == ci_id, model.id <= vid)
                        )
                    ).scalar_one()
                )
            out.append(
                CiVersionInfo(
                    id=int(row.id),
                    seq=seq,
                    name=str(row.name),
                    deployment_state=values.get(int(row.deployment_state_id)),
                    incident_state=values.get(int(row.incident_state_id)),
                    created_at=row.created_at,
                    created_by=int(row.created_by),
                    created_by_name=users.get(int(row.created_by)),
                    change_note=row.change_note,
                )
            )
        return out

    async def versions(
        self, session: AsyncSession, ref: CiRef, *, user_id: int, limit: int, offset: int
    ) -> tuple[list[CiVersionInfo], int]:
        if ref.source != SOURCE or not await _active(session):
            raise CiNotFound(str(ref))
        access = await load_access(session, user_id)
        size = max(1, min(int(limit), _LIMIT_MAX))
        skip = max(0, int(offset))
        service = CiService(session)
        await _guarded(service.get(access, ref.id))  # unreadable or unknown -> CiNotFound
        total = 0
        for model in (TiqoraCmdbCiVersion, TiqoraCmdbCiVersionArchive):
            total += int(
                (
                    await session.execute(
                        select(func.count()).select_from(model).where(model.ci_id == ref.id)
                    )
                ).scalar_one()
            )
        wanted: list[int] = []
        before: int | None = None
        seen = 0
        while len(wanted) < size and seen < min(total, _VERSION_SCAN_MAX):
            page = await _guarded(
                service.list_versions(
                    access, ref.id, before_id=before, limit=100, include_archived=True
                )
            )
            if not page.items:
                break
            for item in page.items:
                if seen >= skip and len(wanted) < size:
                    wanted.append(item.id)
                seen += 1
            before = page.items[-1].id
            if not page.has_more:
                break
        infos = await self._version_infos(session, ref.id, wanted)
        return infos, total

    # --- ticket links ----------------------------------------------------------------------

    async def _ticket_queues(
        self, session: AsyncSession, ticket_ids: Sequence[int]
    ) -> dict[int, int]:
        out: dict[int, int] = {}
        stmt = text("SELECT id, queue_id FROM ticket WHERE id IN :ids").bindparams(
            bindparam("ids", expanding=True)
        )
        for chunk in _chunks(sorted({int(t) for t in ticket_ids})):
            res = await session.execute(stmt, {"ids": list(chunk)})
            out.update({int(i): int(q) for i, q in res.all()})
        return out

    async def _readable_tickets(
        self, session: AsyncSession, user_id: int, ticket_ids: Sequence[int]
    ) -> set[int]:
        queues = await self._ticket_queues(session, ticket_ids)
        perms = PermissionEngine(session)
        allowed: dict[int, bool] = {}
        keep: set[int] = set()
        for tid, queue in queues.items():
            if queue not in allowed:
                allowed[queue] = await perms.check(user_id, queue, "ro")
            if allowed[queue]:
                keep.add(tid)
        return keep

    async def ticket_link_types(self, session: AsyncSession) -> list[LinkTypeOption]:
        if not await _active(session):
            return []
        return [
            LinkTypeOption(
                name=t.name,
                source_label=t.source_label or t.name,
                target_label=t.target_label or t.name,
                pointed=bool(t.pointed),
            )
            for t in await LinkTypeService(session).list_offered()
            if t.offer_ticket_ci
        ]

    async def ticket_links(
        self, session: AsyncSession, ticket_ids: Sequence[int], *, user_id: int
    ) -> dict[int, list[CiTicketLink]]:
        wanted = sorted({int(t) for t in ticket_ids})
        if not wanted or not await _active(session):
            return {}
        allowed = sorted(await self._readable_tickets(session, user_id, wanted))
        if not allowed:
            return {}
        access = await load_access(session, user_id)
        rows: list[Any] = []
        for chunk in _chunks([str(t) for t in allowed]):
            keys = list(chunk)
            res = await session.execute(
                select(TiqoraItsmLink)
                .where(
                    or_(
                        and_(
                            TiqoraItsmLink.source_kind == "ticket",
                            TiqoraItsmLink.source_key.in_(keys),
                            TiqoraItsmLink.target_kind == "ci",
                        ),
                        and_(
                            TiqoraItsmLink.target_kind == "ticket",
                            TiqoraItsmLink.target_key.in_(keys),
                            TiqoraItsmLink.source_kind == "ci",
                        ),
                    )
                )
                .order_by(TiqoraItsmLink.id)
            )
            rows.extend(res.scalars().all())
        pairs: list[tuple[int, int, str, str, datetime]] = []  # ticket, ci, type, side, created
        for row in rows:
            ticket_is_source = row.source_kind == "ticket"
            tkey, ckey = (
                (row.source_key, row.target_key)
                if ticket_is_source
                else (row.target_key, row.source_key)
            )
            if str(tkey).isdigit() and str(ckey).isdigit():
                pairs.append(
                    (
                        int(tkey),
                        int(ckey),
                        str(row.link_type),
                        "source" if ticket_is_source else "target",
                        row.created_at,
                    )
                )
        summaries = await _summaries(session, access, [p[1] for p in pairs])
        pointed = {
            str(n): bool(p)
            for n, p in (
                await session.execute(
                    select(TiqoraItsmLinkType.name, TiqoraItsmLinkType.pointed).where(
                        TiqoraItsmLinkType.name.in_(sorted({p[2] for p in pairs}) or [""])
                    )
                )
            ).all()
        }
        out: dict[int, list[CiTicketLink]] = {}
        for tid, cid, name, side, created in pairs:
            summary = summaries.get(cid)
            if summary is None:
                continue
            direction: Literal["source", "target", "both"] = (
                side if pointed.get(name, False) else "both"  # type: ignore[assignment]
            )
            out.setdefault(tid, []).append(
                CiTicketLink(
                    ticket_id=tid,
                    ci=summary,
                    link_type=name,
                    direction=direction,
                    created_at=created,
                )
            )
        return out

    async def ci_ticket_ids(self, session: AsyncSession, ref: CiRef, *, user_id: int) -> list[int]:
        if ref.source != SOURCE or not await _active(session):
            return []
        access = await load_access(session, user_id)
        await _guarded(CiService(session).get(access, ref.id))
        return sorted(await _ticket_links(session, [ref.id]), reverse=True)

    async def link_ticket(
        self,
        session: AsyncSession,
        *,
        ticket_id: int,
        ref: CiRef,
        link_type: str,
        user_id: int,
    ) -> bool:
        await self._require_active(session)
        if ref.source != SOURCE:
            raise CiNotFound(str(ref))
        access = await load_access(session, user_id)
        name = link_type or _DEFAULT_LINK_TYPE
        ci_end, ticket_end = LinkEnd("ci", str(ref.id)), LinkEnd("ticket", str(ticket_id))
        before = await self._links_between(session, ticket_id, ref.id, name)
        record = await _guarded(
            LinkService(session).add(access, source=ci_end, target=ticket_end, link_type_key=name)
        )
        return not any(r.id == record.id for r in before)

    async def unlink_ticket(
        self,
        session: AsyncSession,
        *,
        ticket_id: int,
        ref: CiRef,
        link_type: str | None,
        user_id: int,
    ) -> int:
        await self._require_active(session)
        if ref.source != SOURCE:
            raise CiNotFound(str(ref))
        access = await load_access(session, user_id)
        await _guarded(CiService(session).get(access, ref.id))
        links = await self._links_between(session, ticket_id, ref.id, link_type)
        service = LinkService(session)
        for row in links:
            await _guarded(service.delete(access, int(row.id)))
        return len(links)

    async def _links_between(
        self, session: AsyncSession, ticket_id: int, ci_id: int, link_type: str | None
    ) -> list[TiqoraItsmLink]:
        t, c = str(ticket_id), str(ci_id)
        stmt = select(TiqoraItsmLink).where(
            or_(
                and_(
                    TiqoraItsmLink.source_kind == "ticket",
                    TiqoraItsmLink.source_key == t,
                    TiqoraItsmLink.target_kind == "ci",
                    TiqoraItsmLink.target_key == c,
                ),
                and_(
                    TiqoraItsmLink.source_kind == "ci",
                    TiqoraItsmLink.source_key == c,
                    TiqoraItsmLink.target_kind == "ticket",
                    TiqoraItsmLink.target_key == t,
                ),
            )
        )
        if link_type is not None:
            stmt = stmt.where(TiqoraItsmLink.link_type == link_type)
        return list((await session.execute(stmt.order_by(TiqoraItsmLink.id))).scalars().all())

    async def _require_active(self, session: AsyncSession) -> None:
        mode = await current_itsm_mode(session)
        if mode != "tiqora":
            raise _gate_error(CmdbUnavailable(mode))

    # --- services --------------------------------------------------------------------------

    async def service_incident_states(
        self, session: AsyncSession, service_ids: Sequence[int]
    ) -> dict[int, ServiceIncidentState]:
        if not service_ids or not await _active(session):
            return {}
        return await _service_incident_states(session, service_ids)

    async def service_cis(
        self, session: AsyncSession, service_id: int, *, user_id: int, limit: int = 100
    ) -> list[CiSummary]:
        if not await _active(session):
            return []
        size = max(1, min(int(limit), _LIMIT_MAX))
        key = str(int(service_id))
        res = await session.execute(
            select(
                TiqoraItsmLink.source_kind,
                TiqoraItsmLink.source_key,
                TiqoraItsmLink.target_key,
            ).where(
                or_(
                    and_(
                        TiqoraItsmLink.source_kind == "service",
                        TiqoraItsmLink.source_key == key,
                        TiqoraItsmLink.target_kind == "ci",
                    ),
                    and_(
                        TiqoraItsmLink.target_kind == "service",
                        TiqoraItsmLink.target_key == key,
                        TiqoraItsmLink.source_kind == "ci",
                    ),
                )
            )
        )
        ci_ids = {
            int(tkey if skind == "service" else skey)
            for skind, skey, tkey in res.all()
            if str(tkey if skind == "service" else skey).isdigit()
        }
        access = await load_access(session, user_id)
        found = await _summaries(session, access, ci_ids)
        return sorted(found.values(), key=lambda s: (s.number, s.ref.id))[:size]


async def _user_names(session: AsyncSession, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    stmt = text("SELECT id, first_name, last_name, login FROM users WHERE id IN :ids").bindparams(
        bindparam("ids", expanding=True)
    )
    out: dict[int, str] = {}
    for i, first, last, login in (await session.execute(stmt, {"ids": sorted(ids)})).all():
        full = f"{first or ''} {last or ''}".strip()
        out[int(i)] = full or str(login)
    return out


def create_tiqora_provider(_config: SysConfig) -> CiProvider:
    return TiqoraCiProvider()


def tiqora_provider_factory() -> CiProviderFactory:
    return CiProviderFactory(mode="tiqora", create=create_tiqora_provider)


__all__ = [
    "TiqoraCiProvider",
    "create_tiqora_provider",
    "tiqora_provider_factory",
]
