"""Ticket ITSM summary (I12): everything the ticket zoom shows about the ITSM side in one call.

``ticket_itsm_summary`` reads, for one ticket the caller may see: the ITSM role (from the ticket
type), criticality / impact and what the priority matrix suggests, the service with its live
incident state, the major-incident declaration or the banner of a linked active major incident,
the linked problem, the CI count, the incident flood warning and which ITSM dialogs have fields.
It is the "single round" behind ``GET /tickets/{id}/itsm``: the number of statements does not
grow with the number of linked tickets or CIs, and while ITSM is off, or the setup rows are
missing, it stops right after the permission check (``enabled=False``).

Also home of the two ITSM dialog screens: ``screen_field_levels`` reads
``Ticket::Frontend::<screen>###DynamicField`` and, when Znuny's ITSM packages are not installed
(key absent), falls back to the lists the packages ship (Task 3 survey, ``ITSM_SCREEN_DEFAULTS``).

Reads only; never commits.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final, Literal

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.itsm.ci_provider import CiUnavailable
from tiqora.itsm.link_graph import TicketNotReadable
from tiqora.itsm.linked_articles import linked_ticket_ids
from tiqora.itsm.major_incident import (
    MajorBanner,
    MajorIncidentError,
    MajorIncidentView,
    active_banners,
    get_major_incident,
)
from tiqora.itsm.mode import ItsmMode, ItsmModeState, get_itsm_mode
from tiqora.itsm.models import TiqoraItsmMajorIncident, TiqoraItsmProblem
from tiqora.itsm.priority import suggest_priority
from tiqora.itsm.providers import get_ci_provider
from tiqora.itsm.services import (
    RecentIncident,
    ServiceItsmInfo,
    ServiceNotFound,
    recent_incident,
    service_info,
)
from tiqora.itsm.settings import ItsmSettings
from tiqora.itsm.setup import setup_complete
from tiqora.itsm.ticket_access import TicketAccessDenied, require_ticket
from tiqora.permissions.engine import PermissionEngine
from tiqora.znuny.sysconfig import SysConfig

Role = Literal["incident", "major_incident", "service_request", "problem", "change", "change_task"]

#: The two dialogs Znuny's ITSM packages add (``ITSMTicket.xml``) and the fields they ship with
#: (Task 3 survey, ``behaviour/itsm-*.json`` ``dynamic_fields_per_screen``): the fallback while
#: the SysConfig key is absent, i.e. without the packages.
ITSM_SCREEN_DEFAULTS: Final[Mapping[str, tuple[str, ...]]] = {
    "AgentTicketAddtlITSMField": ("ITSMRepairStartTime", "ITSMRecoveryStartTime", "ITSMDueDate"),
    "AgentTicketDecision": ("ITSMDecisionResult", "ITSMDecisionDate"),
}
#: Types the agent form can edit (same set as ``GET /reference/dynamic-fields``).
EDITABLE_DF_TYPES: Final = frozenset(
    {"Text", "TextArea", "Dropdown", "Multiselect", "Checkbox", "Date", "DateTime"}
)


@dataclass(frozen=True, slots=True)
class ProblemRef:
    """The problem an incident hangs on (the linked ticket of the problem type)."""

    ticket_id: int
    tn: str
    title: str
    known_error: bool


@dataclass(frozen=True, slots=True)
class Dialogs:
    itsm_fields: bool = False
    decision: bool = False


@dataclass(slots=True)
class TicketItsm:
    enabled: bool
    mode: ItsmMode
    role: Role | None = None
    criticality: str | None = None
    impact: str | None = None
    #: what the matrix suggests for the pair (the UI shows "Matrix: ..." when it differs)
    matrix_priority_id: int | None = None
    service: ServiceItsmInfo | None = None
    major_incident: MajorIncidentView | None = None
    major_banner: MajorBanner | None = None
    problem: ProblemRef | None = None
    #: ``None`` while no provider serves
    ci_count: int | None = None
    recent_incident: RecentIncident | None = None
    dialogs: Dialogs = field(default_factory=Dialogs)


def role_of(type_name: str | None, cfg: ItsmSettings) -> Role | None:
    """The ITSM role of a ticket type name (settings decide the names)."""
    if not type_name:
        return None
    roles: dict[str, Role] = {
        cfg.incident_type: "incident",
        cfg.major_incident_type: "major_incident",
        cfg.service_request_type: "service_request",
        cfg.problem_type: "problem",
        cfg.change_type: "change",
        cfg.change_task_type: "change_task",
    }
    return roles.get(type_name)


async def screen_field_levels(session: AsyncSession, screen: str) -> dict[str, int]:
    """``{field name: 1 shown | 2 required}`` configured for ``screen``.

    The two ITSM dialogs fall back to ``ITSM_SCREEN_DEFAULTS`` when the SysConfig key does not
    exist at all (no Znuny ITSM); an existing but empty setting means "no fields" and wins.
    """
    raw = await SysConfig(session).get(f"Ticket::Frontend::{screen}###DynamicField", None)
    levels: dict[str, int] = {}
    if isinstance(raw, dict):
        for name, value in raw.items():
            try:
                level = int(value)
            except (TypeError, ValueError):
                continue
            if level > 0:
                levels[str(name)] = level
    elif raw is None and screen in ITSM_SCREEN_DEFAULTS:
        levels = dict.fromkeys(ITSM_SCREEN_DEFAULTS[screen], 1)
    return levels


async def _usable_field_names(session: AsyncSession, names: set[str]) -> set[str]:
    """Names among ``names`` that are valid, editable ticket dynamic fields."""
    if not names:
        return set()
    stmt = text(
        "SELECT name FROM dynamic_field WHERE valid_id = 1 AND object_type = 'Ticket'"
        " AND field_type IN :types AND name IN :names"
    ).bindparams(bindparam("types", expanding=True), bindparam("names", expanding=True))
    rows = await session.execute(stmt, {"types": sorted(EDITABLE_DF_TYPES), "names": sorted(names)})
    return {str(r[0]) for r in rows.all()}


async def _dialogs(session: AsyncSession) -> Dialogs:
    configured = {s: await screen_field_levels(session, s) for s in ITSM_SCREEN_DEFAULTS}
    usable = await _usable_field_names(session, {n for lv in configured.values() for n in lv})
    return Dialogs(
        itsm_fields=bool(usable & set(configured["AgentTicketAddtlITSMField"])),
        decision=bool(usable & set(configured["AgentTicketDecision"])),
    )


async def _field_values(
    session: AsyncSession, ticket_id: int, names: list[str]
) -> dict[str, str | None]:
    stmt = text(
        "SELECT df.name, v.value_text FROM dynamic_field df"
        " JOIN dynamic_field_value v ON v.field_id = df.id"
        " WHERE df.object_type = 'Ticket' AND v.object_id = :t AND df.name IN :names"
    ).bindparams(bindparam("names", expanding=True))
    rows = (await session.execute(stmt, {"t": ticket_id, "names": names})).all()
    return {str(n): (None if v is None or str(v) == "" else str(v)) for n, v in rows}


async def _problem_ref(
    session: AsyncSession,
    ticket_id: int,
    *,
    user_id: int,
    cfg: ItsmSettings,
    engine: PermissionEngine,
) -> ProblemRef | None:
    linked = sorted(await linked_ticket_ids(session, ticket_id))
    if not linked:
        return None
    stmt = text(
        "SELECT t.id, t.tn, t.title, t.queue_id FROM ticket t"
        " JOIN ticket_type tt ON tt.id = t.type_id"
        " WHERE t.id IN :ids AND tt.name = :problem ORDER BY t.id"
    ).bindparams(bindparam("ids", expanding=True))
    rows = (await session.execute(stmt, {"ids": linked[:500], "problem": cfg.problem_type})).all()
    for tid, tn, title, queue_id in rows:
        if not await engine.check(user_id, int(queue_id), "ro"):
            continue
        record = await session.get(TiqoraItsmProblem, int(tid))
        return ProblemRef(
            ticket_id=int(tid),
            tn=str(tn),
            title=str(title),
            known_error=bool(record.known_error) if record is not None else False,
        )
    return None


async def ticket_itsm_summary(
    session: AsyncSession,
    ticket_id: int,
    *,
    user_id: int,
    cfg: ItsmSettings,
    mode: ItsmModeState | None = None,
) -> TicketItsm:
    """The ITSM facts of one ticket for ``user_id``. Raises :class:`TicketNotReadable`.

    ``enabled=False`` (mode off, or the ITSM ticket types / state / matrix fields are not set
    up) carries only the mode; no other query runs.
    """
    engine = PermissionEngine(session)  # loads the caller's groups once; shared below
    try:
        facts = await require_ticket(session, ticket_id, user_id=user_id, engine=engine)
    except TicketAccessDenied as exc:
        raise TicketNotReadable(ticket_id) from exc
    service_id = facts.service_id or None
    role = role_of(facts.type_name, cfg)

    state = mode or await get_itsm_mode(session)
    if state.mode == "off" or not await setup_complete(session, cfg):
        return TicketItsm(enabled=False, mode=state.mode)

    out = TicketItsm(enabled=True, mode=state.mode, role=role)
    values = await _field_values(session, ticket_id, [cfg.criticality_field, cfg.impact_field])
    out.criticality = values.get(cfg.criticality_field)
    out.impact = values.get(cfg.impact_field)
    out.matrix_priority_id = await suggest_priority(
        session, state, cfg, criticality=out.criticality, impact=out.impact
    )

    if service_id:
        try:
            out.service = await service_info(session, service_id, user_id=user_id)
        except (ServiceNotFound, CiUnavailable):
            out.service = None
        if role in ("incident", "major_incident"):
            out.recent_incident = await recent_incident(
                session,
                service_id=service_id,
                exclude_ticket_id=ticket_id,
                settings=cfg,
                user_id=user_id,
            )

    if await session.get(TiqoraItsmMajorIncident, ticket_id) is not None:
        try:
            out.major_incident = await get_major_incident(session, ticket_id, user_id=user_id)
        except MajorIncidentError:
            out.major_incident = None
    out.major_banner = (await active_banners(session, [ticket_id], user_id=user_id)).get(ticket_id)
    if role != "problem":
        out.problem = await _problem_ref(
            session, ticket_id, user_id=user_id, cfg=cfg, engine=engine
        )

    provider = get_ci_provider(state.mode, SysConfig(session))
    if provider is not None:
        try:
            links = await provider.ticket_links(session, [ticket_id], user_id=user_id)
            out.ci_count = len(links.get(ticket_id, []))
        except CiUnavailable:
            out.ci_count = None
    out.dialogs = await _dialogs(session)
    return out


__all__ = [
    "EDITABLE_DF_TYPES",
    "ITSM_SCREEN_DEFAULTS",
    "Dialogs",
    "ProblemRef",
    "Role",
    "TicketItsm",
    "TicketNotReadable",
    "role_of",
    "screen_field_levels",
    "ticket_itsm_summary",
]
