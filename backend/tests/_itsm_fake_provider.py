"""In-memory ``CiProvider`` for API and registry tests (no database).

``FakeCiProvider.seeded()`` returns a provider plus the :class:`SeededCis` description the
conformance kit (``itsm_provider_contract``) expects. Users: ``USER_ID`` reads everything,
``RESTRICTED_USER_ID`` (``itsm94-ro``) cannot read the CI named ``hidden``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from tests.itsm_provider_contract import SeededCis
from tiqora.itsm.ci_provider import (
    CatalogValue,
    CiClassInfo,
    CiDetail,
    CiNotFound,
    CiPage,
    CiQuery,
    CiRef,
    CiSource,
    CiSummary,
    CiTicketLink,
    CiVersionInfo,
    LinkTypeOption,
    ServiceIncidentState,
)

USER_ID = 1
RESTRICTED_USER_ID = 94

OPERATIONAL = CatalogValue("1", "Operational", "operational")
WARNING = CatalogValue("2", "Warning", "warning")
INCIDENT = CatalogValue("3", "Incident", "incident")
PRODUCTIVE = CatalogValue("10", "Production", "productive")
_RANK = {"operational": 0, "warning": 1, "incident": 2}
_NOW = datetime(2026, 1, 1, tzinfo=UTC)


class FakeCiProvider:
    read_only = False

    def __init__(self, source: CiSource = "znuny") -> None:
        self.source: CiSource = source
        self._cis: dict[int, CiSummary] = {}
        self._hidden_for: dict[int, set[int]] = {}
        self._versions: dict[int, int] = {}
        self._links: set[tuple[int, int, str]] = set()  # (ticket_id, ci_id, link_type)
        self._services: dict[int, list[int]] = {}

    # -- building ---------------------------------------------------------------------------

    def add_ci(
        self,
        ci_id: int,
        name: str,
        incident: CatalogValue,
        *,
        hidden_for: Sequence[int] = (),
        versions: int = 1,
    ) -> CiRef:
        ref = CiRef(self.source, ci_id)
        self._cis[ci_id] = CiSummary(
            ref=ref,
            number=f"2026{ci_id:04d}",
            name=name,
            class_key="1",
            class_name="Computer",
            deployment_state=PRODUCTIVE,
            incident_state=incident,
            changed_at=_NOW,
            edit_url=None,
        )
        self._hidden_for[ci_id] = set(hidden_for)
        self._versions[ci_id] = versions
        return ref

    def link_service(self, service_id: int, ci_id: int) -> None:
        self._services.setdefault(service_id, []).append(ci_id)

    @classmethod
    def seeded(cls, source: CiSource = "znuny") -> tuple[FakeCiProvider, SeededCis]:
        p = cls(source)
        a = p.add_ci(11, "fake-web-01", OPERATIONAL, versions=4)
        b = p.add_ci(12, "fake-web-02", INCIDENT)
        hidden = p.add_ci(13, "fake-web-hidden", OPERATIONAL, hidden_for=[RESTRICTED_USER_ID])
        p.link_service(5, a.id)
        p.link_service(5, b.id)
        return p, SeededCis(
            user_id=USER_ID,
            restricted_user_id=RESTRICTED_USER_ID,
            a=a,
            b=b,
            hidden=hidden,
            common_text="fake-web",
            a_version_count=4,
            ticket_id=700,
            service_id=5,
            empty_service_id=6,
        )

    # -- helpers ----------------------------------------------------------------------------

    def _visible(self, ci_id: int, user_id: int) -> CiSummary | None:
        ci = self._cis.get(ci_id)
        if ci is None or user_id in self._hidden_for[ci_id]:
            return None
        return ci

    def _require(self, ref: CiRef, user_id: int) -> CiSummary:
        if ref.source != self.source:
            raise CiNotFound(str(ref))
        ci = self._visible(ref.id, user_id)
        if ci is None:
            raise CiNotFound(str(ref))
        return ci

    # -- CiProvider -------------------------------------------------------------------------

    async def is_empty(self, session: AsyncSession) -> bool:
        return not self._cis

    async def classes(self, session: AsyncSession, *, user_id: int) -> list[CiClassInfo]:
        return [CiClassInfo("1", "Computer", None, can_read=True, can_write=False)]

    async def deployment_states(self, session: AsyncSession) -> list[CatalogValue]:
        return [PRODUCTIVE]

    async def incident_states(self, session: AsyncSession) -> list[CatalogValue]:
        return [OPERATIONAL, WARNING, INCIDENT]

    async def search(self, session: AsyncSession, query: CiQuery, *, user_id: int) -> CiPage:
        limit = max(1, min(query.limit, 100))
        needle = (query.text or "").lower()
        hits = [
            ci
            for ci_id, ci in sorted(self._cis.items())
            if self._visible(ci_id, user_id) is not None
            and (not needle or needle in ci.name.lower() or needle in ci.number)
            and (not query.refs or ci.ref in query.refs)
        ]
        return CiPage(items=tuple(hits[query.offset : query.offset + limit]), total=len(hits))

    async def get(
        self,
        session: AsyncSession,
        ref: CiRef,
        *,
        user_id: int,
        version_id: int | None = None,
    ) -> CiDetail:
        summary = self._require(ref, user_id)
        return CiDetail(
            summary=summary,
            version=None,
            attributes=(),
            definition_version=None,
            read_only=self.read_only,
        )

    async def get_many(
        self, session: AsyncSession, refs: Sequence[CiRef], *, user_id: int
    ) -> dict[CiRef, CiSummary]:
        out: dict[CiRef, CiSummary] = {}
        for ref in refs:
            ci = self._visible(ref.id, user_id) if ref.source == self.source else None
            if ci is not None:
                out[ref] = ci
        return out

    async def versions(
        self, session: AsyncSession, ref: CiRef, *, user_id: int, limit: int, offset: int
    ) -> tuple[list[CiVersionInfo], int]:
        ci = self._require(ref, user_id)
        total = self._versions[ref.id]
        rows = [
            CiVersionInfo(
                id=ref.id * 100 + seq,
                seq=seq,
                name=ci.name,
                deployment_state=ci.deployment_state,
                incident_state=ci.incident_state,
                created_at=_NOW,
                created_by=USER_ID,
                created_by_name="Example Admin",
                change_note=None,
            )
            for seq in range(total, 0, -1)
        ]
        return rows[offset : offset + limit], total

    async def ticket_links(
        self, session: AsyncSession, ticket_ids: Sequence[int], *, user_id: int
    ) -> dict[int, list[CiTicketLink]]:
        out: dict[int, list[CiTicketLink]] = {}
        for ticket_id, ci_id, link_type in sorted(self._links):
            ci = self._visible(ci_id, user_id)
            if ticket_id in ticket_ids and ci is not None:
                out.setdefault(ticket_id, []).append(
                    CiTicketLink(ticket_id, ci, link_type, "both", None)
                )
        return out

    async def ci_ticket_ids(self, session: AsyncSession, ref: CiRef, *, user_id: int) -> list[int]:
        self._require(ref, user_id)
        return sorted(t for t, c, _ in self._links if c == ref.id)

    async def ticket_link_types(self, session: AsyncSession) -> list[LinkTypeOption]:
        return [LinkTypeOption("RelevantTo", "Relevant to", "Relevant to", pointed=False)]

    async def link_ticket(
        self, session: AsyncSession, *, ticket_id: int, ref: CiRef, link_type: str, user_id: int
    ) -> bool:
        self._require(ref, user_id)
        key = (ticket_id, ref.id, link_type)
        if key in self._links:
            return False
        self._links.add(key)
        return True

    async def unlink_ticket(
        self,
        session: AsyncSession,
        *,
        ticket_id: int,
        ref: CiRef,
        link_type: str | None,
        user_id: int,
    ) -> int:
        self._require(ref, user_id)
        gone = {
            k
            for k in self._links
            if k[0] == ticket_id and k[1] == ref.id and link_type in (None, k[2])
        }
        self._links -= gone
        return len(gone)

    async def service_incident_states(
        self, session: AsyncSession, service_ids: Sequence[int]
    ) -> dict[int, ServiceIncidentState]:
        out: dict[int, ServiceIncidentState] = {}
        for sid in service_ids:
            states = [
                st
                for cid in self._services.get(sid, [])
                if (st := self._cis[cid].incident_state) is not None
            ]
            worst = max(states, key=lambda s: _RANK[s.functionality or "operational"], default=None)
            out[sid] = ServiceIncidentState(
                service_id=sid,
                state=worst,
                cis_incident=sum(s.functionality == "incident" for s in states),
                cis_warning=sum(s.functionality == "warning" for s in states),
            )
        return out

    async def service_cis(
        self, session: AsyncSession, service_id: int, *, user_id: int, limit: int = 100
    ) -> list[CiSummary]:
        found = (self._visible(cid, user_id) for cid in self._services.get(service_id, []))
        return [ci for ci in found if ci is not None][:limit]


class FakeSession:
    """Stands in for ``AsyncSession`` in provider tests: the fake never touches it."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"FakeCiProvider must not use session.{name}")


__all__ = [
    "RESTRICTED_USER_ID",
    "USER_ID",
    "FakeCiProvider",
    "FakeSession",
]
