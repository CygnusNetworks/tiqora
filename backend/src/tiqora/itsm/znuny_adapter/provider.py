"""``ZnunyCiProvider``: Mode A of the CI contract (I3), Znuny's own CMDB tables read-only.

* **Permission** (behaviour survey, question 6): a CI is visible to a user who holds ``ro`` (``rw``
  counts) on the group in its class's general-catalog preference ``Permission`` (stock: group
  ``itsm-configitem``). Unreadable classes are dropped before any query; an unreadable or unknown
  CI is ``CiNotFound`` for every method.
* **Links** are Znuny's own ``link_relation`` rows. A new ticket-to-CI link stores the ticket as
  source and the CI as target; links Znuny made (the CI as source) are read in both orientations.
  Besides the row, the ticket gets Znuny's ``CILinkAdd`` / ``CILinkDelete`` history entry
  (``%%<ci id>%%<link type>``). Not written, because Mode A never writes the ITSM tables: the CI
  history (``configitem_history``) and ``CurInciStateRecalc``. The latter does not matter for
  ticket links: only CI-to-CI links feed a CI's incident state (survey, question 5).
* **Service incident state** is computed from the stored CI states (survey, question 2): the link
  types of ``ITSM::Core::IncidentLinkTypeDirection`` in the configured direction (reversed for the
  service), productive CIs only, worst functionality wins; a valid direct child service that is not
  operational raises an operational service to warning. A CI's *stored* ``cur_inci_state_id``
  already carries Znuny's transitive "warning" (its recalculation maintains it).

Methods never commit and never write an ITSM table; ``tiqora_*`` tables are only read through
``offered_link_types`` / ``load_itsm_settings`` (separate statements, no JOIN with Znuny tables).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Final, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.itsm import deeplink
from tiqora.itsm.ci_provider import (
    CatalogValue,
    CiClassInfo,
    CiDetail,
    CiError,
    CiNotFound,
    CiPage,
    CiPermissionDenied,
    CiQuery,
    CiRef,
    CiSource,
    CiSummary,
    CiTicketLink,
    CiVersionInfo,
    LinkTypeOption,
    ServiceIncidentState,
)
from tiqora.itsm.link_types import Propagation, offered_link_types, znuny_link_types
from tiqora.itsm.service_state import counts_for_service, fold_service_states, service_closure
from tiqora.itsm.settings import ItsmSettings, load_itsm_settings
from tiqora.itsm.znuny_adapter import attributes as attrs
from tiqora.itsm.znuny_adapter.catalog import (
    catalog_values_by_id,
    class_names,
    class_permission_groups,
    load_catalog,
)
from tiqora.itsm.znuny_adapter.reader import (
    CiRow,
    VersionRow,
    ZnunyItsmReader,
    get_reader,
)
from tiqora.permissions.engine import PermissionEngine
from tiqora.znuny.history import history_add
from tiqora.znuny.sysconfig import SysConfig

if TYPE_CHECKING:
    from tiqora.config import Settings

MAX_PAGE: Final = 100
_RANK: Final = {"operational": 0, "warning": 1, "incident": 2}
_DEFAULT_STATE_NAME: Final = {
    "operational": "Operational",
    "warning": "Warning",
    "incident": "Incident",
}
TICKET_OBJECT: Final = "Ticket"
SERVICE_OBJECT: Final = "Service"

SettingsLoader = Callable[[AsyncSession], Any]


class ZnunyCiProvider:
    source: CiSource = "znuny"
    read_only: bool = True

    def __init__(self, sysconfig: SysConfig, *, settings_loader: SettingsLoader | None = None):
        self._sysconfig = sysconfig
        self._settings_loader = settings_loader

    # ------------------------------------------------------------------ plumbing

    def _cfg(self, session: AsyncSession) -> SysConfig:
        return self._sysconfig if self._sysconfig.session is session else SysConfig(session)

    async def _itsm_settings(self, session: AsyncSession) -> ItsmSettings:
        if self._settings_loader is not None:
            return await self._settings_loader(session)  # type: ignore[no-any-return]
        from tiqora.config import get_settings

        settings: Settings = get_settings()
        return await load_itsm_settings(session, settings)

    async def _readable_classes(
        self, session: AsyncSession, reader: ZnunyItsmReader, user_id: int
    ) -> dict[int, str]:
        """Class id -> name for the classes whose CIs ``user_id`` may read."""
        names = await class_names(session, reader.profile)
        groups = await class_permission_groups(session, reader.profile)
        allowed = await PermissionEngine(session).groups_for_permission(user_id, "ro")
        return {
            cid: name for cid, name in names.items() if groups.get(cid) in allowed and cid in groups
        }

    @staticmethod
    def _znuny_ref(ref: CiRef) -> int:
        if ref.source != "znuny":
            raise CiNotFound(f"CI {ref} does not belong to this source")
        return ref.id

    async def _readable_ci(
        self,
        session: AsyncSession,
        reader: ZnunyItsmReader,
        ref: CiRef,
        classes: Mapping[int, str],
    ) -> CiRow:
        row = await reader.get_ci(session, self._znuny_ref(ref))
        if row is None or row.class_id not in classes:
            raise CiNotFound(f"CI {ref} not found")
        return row

    async def _readable_cis(
        self,
        session: AsyncSession,
        reader: ZnunyItsmReader,
        ci_ids: Sequence[int],
        classes: Mapping[int, str],
    ) -> dict[int, CiRow]:
        rows = await reader.get_cis(session, ci_ids)
        return {i: r for i, r in rows.items() if r.class_id in classes}

    async def _summaries(
        self,
        session: AsyncSession,
        reader: ZnunyItsmReader,
        rows: Sequence[CiRow],
        classes: Mapping[int, str],
    ) -> list[CiSummary]:
        if not rows:
            return []
        versions = await reader.get_versions(
            session, [r.last_version_id for r in rows if r.last_version_id]
        )
        state_ids = {sid for r in rows for sid in (r.cur_depl_state_id, r.cur_inci_state_id) if sid}
        catalog = await catalog_values_by_id(session, state_ids)
        settings = await self._itsm_settings(session)
        cfg = self._cfg(session)
        out: list[CiSummary] = []
        for r in rows:
            version = versions.get(r.last_version_id or 0)
            out.append(
                CiSummary(
                    ref=CiRef("znuny", r.id),
                    number=r.number,
                    name=version.name if version is not None else "",
                    class_key=str(r.class_id),
                    class_name=classes.get(r.class_id, ""),
                    deployment_state=catalog.get(r.cur_depl_state_id or 0),
                    incident_state=catalog.get(r.cur_inci_state_id or 0),
                    changed_at=r.changed_at,
                    edit_url=await deeplink.znuny_agent_url(
                        cfg, settings, deeplink.CI_EDIT, ConfigItemID=r.id
                    ),
                )
            )
        return out

    async def _version_infos(
        self,
        session: AsyncSession,
        rows: Sequence[VersionRow],
        seqs: Sequence[int],
    ) -> list[CiVersionInfo]:
        catalog = await catalog_values_by_id(
            session, {s for v in rows for s in (v.depl_state_id, v.inci_state_id) if s}
        )
        names = await attrs.user_full_names(session, {v.created_by for v in rows if v.created_by})
        return [
            CiVersionInfo(
                id=v.id,
                seq=seq,
                name=v.name,
                deployment_state=catalog.get(v.depl_state_id or 0),
                incident_state=catalog.get(v.inci_state_id or 0),
                created_at=v.created_at,
                created_by=v.created_by,
                created_by_name=names.get(v.created_by) if v.created_by else None,
                change_note=None,
            )
            for v, seq in zip(rows, seqs, strict=True)
        ]

    # ------------------------------------------------------------------ catalogues

    async def is_empty(self, session: AsyncSession) -> bool:
        await get_reader(session)  # CiUnavailable while the tables are missing / unsurveyed
        found = (await session.execute(text("SELECT 1 FROM configitem LIMIT 1"))).first()
        return found is None

    async def classes(self, session: AsyncSession, *, user_id: int) -> list[CiClassInfo]:
        reader = await get_reader(session)
        readable = await self._readable_classes(session, reader, user_id)
        return [
            CiClassInfo(key=str(cid), name=name, icon=None, can_read=True, can_write=False)
            for cid, name in sorted(readable.items(), key=lambda kv: (kv[1].lower(), kv[0]))
        ]

    async def deployment_states(self, session: AsyncSession) -> list[CatalogValue]:
        reader = await get_reader(session)
        return await load_catalog(session, reader.profile, "deployment_state")

    async def incident_states(self, session: AsyncSession) -> list[CatalogValue]:
        reader = await get_reader(session)
        return await load_catalog(session, reader.profile, "incident_state")

    # ------------------------------------------------------------------ read

    async def search(self, session: AsyncSession, query: CiQuery, *, user_id: int) -> CiPage:
        reader = await get_reader(session)
        readable = await self._readable_classes(session, reader, user_id)
        outcome = await reader.search_ex(session, query, class_ids=readable.keys())
        items = await self._summaries(session, reader, outcome.rows, readable)
        return CiPage(items=tuple(items), total=outcome.total, truncated=outcome.truncated)

    async def get(
        self,
        session: AsyncSession,
        ref: CiRef,
        *,
        user_id: int,
        version_id: int | None = None,
    ) -> CiDetail:
        reader = await get_reader(session)
        readable = await self._readable_classes(session, reader, user_id)
        row = await self._readable_ci(session, reader, ref, readable)
        (summary,) = await self._summaries(session, reader, [row], readable)
        shown: VersionRow | None
        if version_id is None:
            shown = await reader.get_version(session, row.last_version_id or 0)
        else:
            shown = await reader.get_version(session, version_id)
            if shown is None or shown.ci_id != row.id:
                raise CiNotFound(f"version {version_id} of CI {ref} not found")
        if shown is None:
            return CiDetail(
                summary=summary,
                version=None,
                attributes=(),
                definition_version=None,
                read_only=True,
            )
        seq = int(
            (
                await session.execute(
                    text(
                        "SELECT COUNT(*) FROM configitem_version"
                        " WHERE configitem_id = :ci AND id <= :v"
                    ),
                    {"ci": row.id, "v": shown.id},
                )
            ).scalar_one()
        )
        (info,) = await self._version_infos(session, [shown], [seq])
        definition = (await reader.definitions(session, [shown.definition_id])).get(
            shown.definition_id
        )
        attributes = await reader.display_attributes(
            session, shown, readable_class_ids=readable.keys()
        )
        return CiDetail(
            summary=summary,
            version=info,
            attributes=attributes,
            definition_version=definition.version if definition is not None else None,
            read_only=True,
        )

    async def get_many(
        self, session: AsyncSession, refs: Sequence[CiRef], *, user_id: int
    ) -> dict[CiRef, CiSummary]:
        ids = sorted({r.id for r in refs if r.source == "znuny"})
        if not ids:
            return {}
        reader = await get_reader(session)
        readable = await self._readable_classes(session, reader, user_id)
        rows = await self._readable_cis(session, reader, ids, readable)
        summaries = await self._summaries(session, reader, list(rows.values()), readable)
        return {s.ref: s for s in summaries}

    async def versions(
        self, session: AsyncSession, ref: CiRef, *, user_id: int, limit: int, offset: int
    ) -> tuple[list[CiVersionInfo], int]:
        reader = await get_reader(session)
        readable = await self._readable_classes(session, reader, user_id)
        row = await self._readable_ci(session, reader, ref, readable)
        offset = max(0, offset)
        rows, total = await reader.versions(session, row.id, limit=limit, offset=offset)
        seqs = [total - offset - index for index in range(len(rows))]
        return await self._version_infos(session, rows, seqs), total

    # ------------------------------------------------------------------ ticket links

    async def ticket_links(
        self, session: AsyncSession, ticket_ids: Sequence[int], *, user_id: int
    ) -> dict[int, list[CiTicketLink]]:
        if not ticket_ids:
            return {}
        reader = await get_reader(session)
        links = await reader.object_ci_links(
            session, object_name=TICKET_OBJECT, object_ids=set(ticket_ids)
        )
        if not links:
            return {}
        readable = await self._readable_classes(session, reader, user_id)
        cis = await self._readable_cis(
            session, reader, sorted({lk.ci_id for lk in links}), readable
        )
        summaries = {
            s.ref.id: s
            for s in await self._summaries(session, reader, list(cis.values()), readable)
        }
        pointed = {t.name: t.pointed for t in (await znuny_link_types(self._cfg(session))).values()}
        out: dict[int, list[CiTicketLink]] = defaultdict(list)
        for lk in links:
            summary = summaries.get(lk.ci_id)
            if summary is None:
                continue
            direction: Literal["source", "target", "both"]
            if not pointed.get(lk.link_type, False):
                direction = "both"
            else:
                direction = "source" if lk.object_is_source else "target"
            out[lk.object_id].append(
                CiTicketLink(
                    ticket_id=lk.object_id,
                    ci=summary,
                    link_type=lk.link_type,
                    direction=direction,
                    created_at=lk.created_at,
                )
            )
        return dict(out)

    async def ci_ticket_ids(self, session: AsyncSession, ref: CiRef, *, user_id: int) -> list[int]:
        reader = await get_reader(session)
        readable = await self._readable_classes(session, reader, user_id)
        row = await self._readable_ci(session, reader, ref, readable)
        links = await reader.object_ci_links(session, object_name=TICKET_OBJECT, ci_id=row.id)
        return sorted({lk.object_id for lk in links})

    async def ticket_link_types(self, session: AsyncSession) -> list[LinkTypeOption]:
        options = await offered_link_types(session, pair="ticket_ci", mode="znuny_readthrough")
        return [
            LinkTypeOption(
                name=o.name,
                source_label=o.source_label or o.source_name or o.name,
                target_label=o.target_label or o.target_name or o.name,
                pointed=o.pointed,
            )
            for o in options
        ]

    @staticmethod
    async def _require_ticket_rw(session: AsyncSession, ticket_id: int, user_id: int) -> None:
        """R12: linking needs ``rw`` on the ticket's queue group, checked before any write."""
        queue_id = (
            await session.execute(
                text("SELECT queue_id FROM ticket WHERE id = :t"), {"t": ticket_id}
            )
        ).scalar_one_or_none()
        if queue_id is None:
            raise CiNotFound(f"ticket {ticket_id} does not exist")
        if not await PermissionEngine(session).check(user_id, int(queue_id), "rw"):
            raise CiPermissionDenied(f"rw on ticket {ticket_id} is required to link CIs")

    async def link_ticket(
        self,
        session: AsyncSession,
        *,
        ticket_id: int,
        ref: CiRef,
        link_type: str,
        user_id: int,
    ) -> bool:
        from tiqora.domain.ticket_write_service import LinkConflict, link_objects

        reader = await get_reader(session)
        readable = await self._readable_classes(session, reader, user_id)
        row = await self._readable_ci(session, reader, ref, readable)
        await self._require_ticket_rw(session, ticket_id, user_id)
        offered = {
            o.name
            for o in await offered_link_types(session, pair="ticket_ci", mode="znuny_readthrough")
        }
        if link_type not in offered:
            raise CiError(f"link type {link_type!r} is not offered between a ticket and a CI")
        try:
            created = await link_objects(
                session,
                source_object=TICKET_OBJECT,
                source_key=str(ticket_id),
                target_object=reader.profile.ci_link_object,
                target_key=str(row.id),
                link_type=link_type,
                user_id=user_id,
            )
        except LinkConflict as exc:
            if exc.reason == "opposite_direction":
                return False  # Znuny made it the other way round: the link exists
            raise CiError(f"link refused: {exc.reason}") from exc
        if created:
            await history_add(
                session,
                ticket_id=ticket_id,
                history_type="CILinkAdd",
                name=f"%%{row.id}%%{link_type}",
                user_id=user_id,
            )
        return created

    async def unlink_ticket(
        self,
        session: AsyncSession,
        *,
        ticket_id: int,
        ref: CiRef,
        link_type: str | None,
        user_id: int,
    ) -> int:
        from tiqora.domain.ticket_write_service import unlink_objects

        reader = await get_reader(session)
        readable = await self._readable_classes(session, reader, user_id)
        row = await self._readable_ci(session, reader, ref, readable)
        await self._require_ticket_rw(session, ticket_id, user_id)
        existing = await reader.object_ci_links(
            session,
            object_name=TICKET_OBJECT,
            object_ids=[ticket_id],
            ci_id=row.id,
            valid_only=False,
        )
        types = sorted({lk.link_type for lk in existing if link_type in (None, lk.link_type)})
        # Znuny writes no history for temporary links (survey q5)
        temporary_only = {
            name
            for name in types
            if all(lk.state == "Temporary" for lk in existing if lk.link_type == name)
        }
        removed = 0
        for name in types:
            count = await unlink_objects(
                session,
                object1=TICKET_OBJECT,
                key1=str(ticket_id),
                object2=reader.profile.ci_link_object,
                key2=str(row.id),
                link_type=name,
                user_id=user_id,
            )
            if count:
                removed += count
                if name in temporary_only:
                    continue
                await history_add(
                    session,
                    ticket_id=ticket_id,
                    history_type="CILinkDelete",
                    name=f"%%{row.id}%%{name}",
                    user_id=user_id,
                )
        return removed

    # ------------------------------------------------------------------ services

    async def service_cis(
        self, session: AsyncSession, service_id: int, *, user_id: int, limit: int = 100
    ) -> list[CiSummary]:
        reader = await get_reader(session)
        links = await reader.object_ci_links(
            session, object_name=SERVICE_OBJECT, object_ids=[service_id]
        )
        if not links:
            return []
        readable = await self._readable_classes(session, reader, user_id)
        rows = await self._readable_cis(
            session, reader, sorted({lk.ci_id for lk in links}), readable
        )
        summaries = await self._summaries(session, reader, list(rows.values()), readable)
        summaries.sort(key=lambda s: (s.name.lower(), s.ref.id))
        return summaries[: max(1, min(limit, 500))]

    async def service_incident_states(
        self, session: AsyncSession, service_ids: Sequence[int]
    ) -> dict[int, ServiceIncidentState]:
        wanted = sorted({int(s) for s in service_ids})
        if not wanted:
            return {}
        reader = await get_reader(session)
        names: dict[int, tuple[str, bool]] = {
            int(sid): (str(name), int(valid) == 1)
            for sid, name, valid in (
                await session.execute(text("SELECT id, name, valid_id FROM service"))
            ).all()
        }
        closure, children_of = service_closure(names, wanted)

        links = (
            await reader.object_ci_links(session, object_name=SERVICE_OBJECT, object_ids=closure)
            if closure
            else []
        )
        cfg = self._cfg(session)
        link_types = await znuny_link_types(cfg)
        cis = await reader.get_cis(session, sorted({lk.ci_id for lk in links}))
        catalog = await catalog_values_by_id(
            session,
            {s for c in cis.values() for s in (c.cur_depl_state_id, c.cur_inci_state_id) if s},
        )
        incident_values = await load_catalog(session, reader.profile, "incident_state")

        def functionality(state_id: int | None) -> str | None:
            value = catalog.get(state_id or 0)
            return value.functionality if value is not None else None

        linked: dict[int, set[int]] = defaultdict(set)
        counted: dict[int, set[int]] = defaultdict(set)
        for lk in links:
            ci = cis.get(lk.ci_id)
            if ci is None:
                continue
            linked[lk.object_id].add(ci.id)
            zt = link_types.get(lk.link_type)
            prop: Propagation = zt.propagation if zt is not None else "none"
            if (
                zt is not None
                and counts_for_service(prop, zt.pointed, lk.object_is_source)
                and functionality(ci.cur_depl_state_id) == "productive"
            ):
                counted[lk.object_id].add(ci.id)

        folded = fold_service_states(
            names,
            children_of,
            closure,
            {
                sid: [functionality(cis[c].cur_inci_state_id) for c in ci_ids]
                for sid, ci_ids in counted.items()
            },
            {sid: bool(ci_ids) for sid, ci_ids in linked.items()},
        )

        def pick(kind: str) -> CatalogValue | None:
            of_kind = sorted(
                (v for v in incident_values if v.functionality == kind), key=lambda v: int(v.key)
            )
            default = next((v for v in of_kind if v.name == _DEFAULT_STATE_NAME[kind]), None)
            return default or (of_kind[0] if of_kind else None)

        out: dict[int, ServiceIncidentState] = {}
        for sid in wanted:
            fold = folded.get(sid)
            if fold is None:
                out[sid] = ServiceIncidentState(sid, None, 0, 0)
                continue
            shown = fold.shown
            out[sid] = ServiceIncidentState(
                service_id=sid,
                state=None if shown is None else pick(shown),
                cis_incident=fold.cis_incident,
                cis_warning=fold.cis_warning,
            )
        return out


__all__ = ["ZnunyCiProvider"]
