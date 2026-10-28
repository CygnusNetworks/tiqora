"""Where the legacy (Znuny) changes are read from, and what of their links a caller may see.

Mode A with the change tables -> :class:`ZnunyLiveSource` (the live Znuny rows); Mode B reads
the archive the importer stored (added with the archive); every other case has no source. The
sources do not check permissions: the route requires the legacy change group, and
:func:`visible_links` leaves out linked tickets and CIs the caller cannot read (ruling F20).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.config import Settings
from tiqora.domain.ticket_access import readable_tickets
from tiqora.itsm.change.legacy import reader
from tiqora.itsm.change.legacy.model import LegacyChange, LegacyChangeSummary
from tiqora.itsm.ci_provider import CiProvider, CiRef
from tiqora.itsm.mode import get_itsm_mode

SourceKind = Literal["live", "archive"]


class LegacyChangeSource(Protocol):
    kind: SourceKind

    async def search(
        self,
        session: AsyncSession,
        *,
        text: str | None,
        ticket_id: int | None,
        ci_ref: CiRef | None,
        limit: int,
        offset: int,
    ) -> tuple[list[LegacyChangeSummary], int]: ...

    async def get(self, session: AsyncSession, znuny_change_id: int) -> LegacyChange | None: ...


class ZnunyLiveSource:
    """The changes as they are in Znuny now (Mode A); reads only."""

    kind: SourceKind = "live"

    async def search(
        self,
        session: AsyncSession,
        *,
        text: str | None,
        ticket_id: int | None,
        ci_ref: CiRef | None,
        limit: int,
        offset: int,
    ) -> tuple[list[LegacyChangeSummary], int]:
        if ci_ref is not None and ci_ref.source != "znuny":
            return [], 0  # a Tiqora CI is never linked to a Znuny work order
        return await reader.search(
            session,
            text=text,
            ticket_id=ticket_id,
            ci_znuny_id=None if ci_ref is None else ci_ref.id,
            limit=limit,
            offset=offset,
        )

    async def get(self, session: AsyncSession, znuny_change_id: int) -> LegacyChange | None:
        found = await reader.read_changes(session, [znuny_change_id])
        return found[0] if found else None


async def get_legacy_source(session: AsyncSession, settings: Settings) -> LegacyChangeSource | None:
    """Mode A + change tables -> the live source; otherwise ``None`` (the Mode B archive source
    comes with the archive)."""
    _ = settings
    state = await get_itsm_mode(session)
    if state.mode == "znuny_readthrough" and await reader.change_tables_present(session):
        return ZnunyLiveSource()
    return None


# --- links the caller may see ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class VisibleTicketLink:
    ticket_id: int
    tn: str
    title: str
    state: str
    link_type: str
    direction: Literal["source", "target"]
    workorder_number: int


@dataclass(frozen=True, slots=True)
class VisibleCiLink:
    ref: str
    #: ``None`` for a CI that is only known by its Znuny id (archive of a CI not imported)
    number: str | None
    name: str | None
    class_name: str | None
    link_type: str
    direction: Literal["source", "target"]
    workorder_number: int


@dataclass(frozen=True, slots=True)
class VisibleServiceLink:
    service_id: int
    #: ``None`` when the service no longer exists
    name: str | None
    link_type: str
    direction: Literal["source", "target"]
    workorder_number: int


@dataclass(frozen=True, slots=True)
class VisibleLinks:
    tickets: tuple[VisibleTicketLink, ...]
    cis: tuple[VisibleCiLink, ...]
    services: tuple[VisibleServiceLink, ...]
    #: linked tickets/CIs left out because the caller cannot read them (a number only)
    hidden: int


def _ci_ref(key: str) -> CiRef | None:
    """A live link key is the Znuny CI id; an archived one is ``tiqora:<id>`` / ``znuny:<id>``."""
    if key.isdigit():
        return CiRef("znuny", int(key))
    try:
        return CiRef.parse(key)
    except ValueError:
        return None


async def visible_links(
    session: AsyncSession,
    change: LegacyChange,
    *,
    user_id: int,
    provider: CiProvider | None,
) -> VisibleLinks:
    """The change's links with titles/names, without what the caller cannot read.

    Tickets need ``ro`` on their queue (``readable_tickets``); CIs of the provider's id space go
    through ``provider.get_many`` (unreadable or missing ones are left out); a CI of the other id
    space (an archived ``znuny:<id>`` the import did not take over) is shown by its id only.
    Links to other objects are not shown.
    """
    ticket_links = [
        li for li in change.links if li.object == reader.TICKET_OBJECT and li.key.isdigit()
    ]
    ci_links = [(li, _ci_ref(li.key)) for li in change.links if li.object == reader.CI_OBJECT]
    service_links = [
        li for li in change.links if li.object == reader.SERVICE_OBJECT and li.key.isdigit()
    ]
    hidden = 0

    facts = await readable_tickets(session, [int(li.key) for li in ticket_links], user_id=user_id)
    tickets: list[VisibleTicketLink] = []
    for li in ticket_links:
        found = facts.get(int(li.key))
        if found is None:
            hidden += 1
            continue
        tickets.append(
            VisibleTicketLink(
                ticket_id=found.ticket_id,
                tn=found.tn,
                title=found.title,
                state=found.state,
                link_type=li.link_type,
                direction=li.direction,
                workorder_number=li.workorder_number,
            )
        )

    own_source = None if provider is None else provider.source
    own = sorted({ref for _, ref in ci_links if ref is not None and ref.source == own_source})
    summaries = await provider.get_many(session, own, user_id=user_id) if provider and own else {}
    cis: list[VisibleCiLink] = []
    for li, ref in ci_links:
        if ref is None or provider is None:
            hidden += 1
            continue
        shown: tuple[str | None, str | None, str | None] = (None, None, None)
        if ref.source == own_source:
            summary = summaries.get(ref)
            if summary is None:
                hidden += 1
                continue
            shown = (summary.number, summary.name, summary.class_name)
        cis.append(
            VisibleCiLink(
                ref=str(ref),
                number=shown[0],
                name=shown[1],
                class_name=shown[2],
                link_type=li.link_type,
                direction=li.direction,
                workorder_number=li.workorder_number,
            )
        )

    names = await reader.service_names(session, {int(li.key) for li in service_links})
    services = tuple(
        VisibleServiceLink(
            service_id=int(li.key),
            name=names.get(int(li.key)),
            link_type=li.link_type,
            direction=li.direction,
            workorder_number=li.workorder_number,
        )
        for li in service_links
    )
    return VisibleLinks(tuple(tickets), tuple(cis), services, hidden)
