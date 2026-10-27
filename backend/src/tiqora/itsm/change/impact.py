"""Stored impact set of a change, the job that computes it and the filtered read (design I9).

The affected set of a change is its linked CIs plus everything an incident there would reach
(``cmdb.impact.affected_set``), and its services (the ticket's service, the extra services and the
services the incident travels to). It is computed once, in system scope, by the job
``core.itsm_change_impact`` and stored in ``tiqora_itsm_change_impact`` (replaced as a whole);
risk and conflict checks read the stored rows. Only :func:`read_impact` filters by the caller: a
CI of a class the caller may not read is left out and counted, an unreadable incident ticket is
left out.

Re-computation is requested by :func:`enqueue_impact`. Each request moves ``impact_requested_at``
strictly forward (whole seconds: MariaDB ``DATETIME`` drops fractions, so two requests in one
second would be indistinguishable otherwise). A pass remembers the request it answers, stores
``max(that request, start - 1 s)`` as ``impact_computed_at`` and, when the stamp moved while it
ran, the job runs another pass (at most :data:`MAX_PASSES`). A request stamped after the pass
started is greater than the stored computation time; one that was stamped earlier but committed
after the pass read the row is caught by the loop's re-read. When the loop gives up (three
passes) or such a request joined a job that was already past its re-read, ``impact_requested_at``
stays above ``impact_computed_at`` and the next refresh tick queues the change again.

Nothing here commits except the job; no JOIN between ``tiqora_*`` and Znuny tables.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final

from pydantic import BaseModel
from sqlalchemy import bindparam, case, delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.cmdb.impact import affected_set, services_reached_from
from tiqora.cmdb.links import LinkService
from tiqora.cmdb.propagation import service_incident_states
from tiqora.cmdb.settings import load_cmdb_settings
from tiqora.config import Settings
from tiqora.db.tiqora.models import TiqoraCmdbCi
from tiqora.domain.queue_service import OPEN_STATE_TYPES
from tiqora.domain.schemas import UtcDateTime
from tiqora.domain.ticket_access import CHUNK, readable_tickets
from tiqora.itsm.change.errors import ChangeError
from tiqora.itsm.change.gate import require_change_mode
from tiqora.itsm.change.models import TiqoraItsmChange, TiqoraItsmChangeImpact
from tiqora.itsm.change.phases import REOPENABLE_PHASES
from tiqora.itsm.change.service import (
    ChangeEvent,
    _extra_services,
    _row,
    _ticket,
    notify_listeners,
    record_event,
)
from tiqora.itsm.change.settings import ItsmChangeSettings
from tiqora.itsm.ci_provider import CatalogValue, CiRef
from tiqora.itsm.providers import get_ci_provider
from tiqora.itsm.settings import load_itsm_settings
from tiqora.jobs.context import JobContext
from tiqora.jobs.registry import JobHandler
from tiqora.jobs.service import enqueue_job, utc_now
from tiqora.znuny.sysconfig import SysConfig

logger = logging.getLogger(__name__)

ITSM_CHANGE_IMPACT_KIND: Final = "core.itsm_change_impact"
#: a request that arrives while the job runs makes it run again, at most this often
MAX_PASSES: Final = 3
#: changes the worker tick queues per run
REFRESH_LIMIT: Final = 200
#: open incident tickets listed on a change
INCIDENT_LIST_CAP: Final = 50
#: incident rows read before the permission filter
INCIDENT_SCAN_CAP: Final = 500
SYSTEM_USER_ID: Final = 1
_STAMPED_KEY: Final = "itsm_change_impact_stamped"


# --- recompute -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ImpactResult:
    ci_count: int
    direct_ci_count: int
    service_count: int
    truncated: bool
    #: the stored rows differ from the ones before (always true for the first computation)
    changed: bool
    #: ``impact_requested_at`` as this pass saw it
    requested: datetime | None


async def _existing_cis(session: AsyncSession, ci_ids: set[int]) -> set[int]:
    found: set[int] = set()
    wanted = sorted(ci_ids)
    for start in range(0, len(wanted), CHUNK):
        rows = await session.execute(
            select(TiqoraCmdbCi.id).where(TiqoraCmdbCi.id.in_(wanted[start : start + CHUNK]))
        )
        found.update(int(r[0]) for r in rows)
    return found


async def recompute_impact(
    session: AsyncSession, *, ticket_id: int, user_id: int | None = None
) -> ImpactResult | None:
    """Replace the stored affected set of a change (flushes, never commits).

    Direct CIs are the ones linked to the ticket; direct services are the ticket's service and
    the change's extra services. Returns ``None`` for a ticket without a change record. An
    unchanged set writes no rows, no ``impact`` event and calls no listener, so the periodic
    refresh leaves the timeline alone.
    """
    started = utc_now().replace(microsecond=0)
    row = await session.get(TiqoraItsmChange, ticket_id)
    if row is None:
        return None
    requested = row.impact_requested_at
    # a request that arrives later gets a stamp above this (see ``_next_stamp``)
    snapshot = started - timedelta(seconds=1)
    if requested is not None and requested > snapshot:
        snapshot = requested

    linked = (await LinkService(session).ci_ids_for_tickets([ticket_id])).get(ticket_id, set())
    direct_cis = await _existing_cis(session, linked)
    own = (
        await session.execute(text("SELECT service_id FROM ticket WHERE id = :t"), {"t": ticket_id})
    ).first()
    direct_services = set(await _extra_services(session, ticket_id))
    if own is not None and own[0] is not None:
        direct_services.add(int(own[0]))

    reach = await affected_set(
        session, sorted(direct_cis), settings=await load_cmdb_settings(session)
    )
    new: set[tuple[str, str, bool]] = set()
    for ci_id in reach.ci_ids | direct_cis:
        new.add(("ci", str(ci_id), ci_id in direct_cis))
    for service_id in reach.service_ids | direct_services:
        new.add(("service", str(service_id), service_id in direct_services))

    old = {
        (str(kind), str(key), bool(direct))
        for kind, key, direct in (
            await session.execute(
                select(
                    TiqoraItsmChangeImpact.kind,
                    TiqoraItsmChangeImpact.ref_key,
                    TiqoraItsmChangeImpact.direct,
                ).where(TiqoraItsmChangeImpact.ticket_id == ticket_id)
            )
        ).all()
    }
    first = row.impact_computed_at is None
    changed = first or new != old
    if new != old:
        await session.execute(
            delete(TiqoraItsmChangeImpact).where(TiqoraItsmChangeImpact.ticket_id == ticket_id)
        )
        session.add_all(
            TiqoraItsmChangeImpact(ticket_id=ticket_id, kind=kind, ref_key=key, direct=direct)
            for kind, key, direct in sorted(new)
        )
    truncated = bool(reach.truncated)
    row.impact_computed_at = snapshot
    row.impact_truncated = truncated
    await session.flush()

    result = ImpactResult(
        ci_count=sum(1 for k in new if k[0] == "ci"),
        direct_ci_count=len(direct_cis),
        service_count=sum(1 for k in new if k[0] == "service"),
        truncated=truncated,
        changed=changed,
        requested=requested,
    )
    if changed:
        actor = user_id if user_id is not None else SYSTEM_USER_ID
        # counts only (ruling F26): ids are read, filtered, from the stored rows
        await record_event(
            session,
            ticket_id=ticket_id,
            event="impact",
            user_id=actor,
            detail={
                "cis": result.ci_count,
                "services": result.service_count,
                "truncated": truncated,
            },
        )
        await notify_listeners(
            session, ChangeEvent(ticket_id, "impact", row.phase, row.phase, actor, utc_now())
        )
    return result


# --- queueing --------------------------------------------------------------------------------


def _stamp_once(session: AsyncSession, row: TiqoraItsmChange) -> None:
    """Move the request stamp forward, once per transaction and change.

    Stamps written inside one transaction are invisible to the job until it commits, so one is
    enough (``create_change`` links up to 50 CIs). Tracked in ``session.info`` by transaction,
    because the flush in ``enqueue_job`` resets the attribute history.
    """
    txn = session.sync_session.get_transaction()
    seen = session.info.get(_STAMPED_KEY)
    if seen is None or seen[0] is not txn:
        seen = (txn, set())
        session.info[_STAMPED_KEY] = seen
    if row.ticket_id in seen[1]:
        return
    seen[1].add(row.ticket_id)
    row.impact_requested_at = _next_stamp(row.impact_requested_at)


def _next_stamp(old: datetime | None) -> datetime:
    """A request time in whole seconds that is greater than every earlier one."""
    now = utc_now().replace(microsecond=0)
    return now if old is None or now > old else old + timedelta(seconds=1)


async def _enqueue(
    session: AsyncSession, *, ticket_id: int, user_id: int | None, mark_requested: bool
) -> int | None:
    row = await session.get(TiqoraItsmChange, ticket_id)
    if row is None or row.phase not in REOPENABLE_PHASES:
        return None
    if mark_requested:
        # also when a job is queued or running already: its loop and the guard see the request
        _stamp_once(session, row)
    job = await enqueue_job(
        session,
        kind=ITSM_CHANGE_IMPACT_KIND,
        payload=ChangeImpactPayload(ticket_id=ticket_id, user_id=user_id),
        created_by=user_id,
        dedupe_key=f"change:{ticket_id}",
    )
    return int(job.id)


async def enqueue_impact(
    session: AsyncSession, *, ticket_id: int, user_id: int | None
) -> int | None:
    """Ask for a fresh impact set (flushes, never commits); the job id, or ``None`` for a ticket
    without a change record or a change in a final phase. One active job per change."""
    return await _enqueue(session, ticket_id=ticket_id, user_id=user_id, mark_requested=True)


async def enqueue_impact_if_change(session: AsyncSession, ticket_id: int) -> None:
    """Called when a ticket gets or loses a CI link (``cmdb.links.signal_ticket_link``)."""
    if await session.get(TiqoraItsmChange, ticket_id) is None:
        return
    await enqueue_impact(session, ticket_id=ticket_id, user_id=None)


async def refresh_stale_impacts(session: AsyncSession, cfg: ItsmChangeSettings) -> int:
    """Queue the active changes whose impact is older than ``impact_refresh_hours`` (or was never
    computed), oldest first, at most :data:`REFRESH_LIMIT` per call. Returns how many.

    Also picks changes with a request newer than the computation (a request the job missed, or
    the three-pass bail-out). The refresh does not stamp ``impact_requested_at`` itself: a valid
    stored set stays valid meanwhile.
    """
    cutoff = utc_now() - timedelta(hours=cfg.impact_refresh_hours)
    computed = TiqoraItsmChange.impact_computed_at
    ids = (
        (
            await session.execute(
                select(TiqoraItsmChange.ticket_id)
                .where(
                    TiqoraItsmChange.phase.in_(sorted(REOPENABLE_PHASES)),
                    (computed.is_(None))
                    | (computed < cutoff)
                    | (TiqoraItsmChange.impact_requested_at > computed),
                )
                .order_by(
                    case(
                        (computed.is_(None), 0),
                        (TiqoraItsmChange.impact_requested_at > computed, 0),
                        else_=1,
                    ),
                    computed,
                    TiqoraItsmChange.ticket_id,
                )
                .limit(REFRESH_LIMIT)
            )
        )
        .scalars()
        .all()
    )
    queued = 0
    for ticket_id in ids:
        if (
            await _enqueue(session, ticket_id=int(ticket_id), user_id=None, mark_requested=False)
            is not None
        ):
            queued += 1
    return queued


# --- job -------------------------------------------------------------------------------------


class ChangeImpactPayload(BaseModel):
    ticket_id: int
    user_id: int | None = None


async def run_change_impact_job(ctx: JobContext, payload: ChangeImpactPayload) -> dict[str, Any]:
    """Compute the impact set; run again (up to :data:`MAX_PASSES`) while a request arrived
    during the pass. Result: counts only."""
    result: dict[str, Any] = {"passes": 0}
    for _ in range(MAX_PASSES):
        async with ctx.session_factory() as session:
            phase = (
                await session.execute(
                    select(TiqoraItsmChange.phase).where(
                        TiqoraItsmChange.ticket_id == payload.ticket_id
                    )
                )
            ).scalar_one_or_none()
            outcome = (
                await recompute_impact(
                    session, ticket_id=payload.ticket_id, user_id=payload.user_id
                )
                if phase in REOPENABLE_PHASES
                else None
            )
            await session.commit()
        if outcome is None:
            result["skipped"] = True
            break
        result.update(
            passes=int(result["passes"]) + 1,
            cis=outcome.ci_count,
            services=outcome.service_count,
            truncated=outcome.truncated,
        )
        async with ctx.session_factory() as session:
            requested = (
                await session.execute(
                    select(TiqoraItsmChange.impact_requested_at).where(
                        TiqoraItsmChange.ticket_id == payload.ticket_id
                    )
                )
            ).scalar_one_or_none()
        if requested == outcome.requested:
            break
    return result


ITSM_CHANGE_IMPACT_JOB = JobHandler(
    kind=ITSM_CHANGE_IMPACT_KIND,
    payload_model=ChangeImpactPayload,
    run=run_change_impact_job,
    title_key="jobs.kinds.core.itsm_change_impact",
    timeout_seconds=600,
    max_attempts=3,
    requeue_if_interrupted=True,
)


# --- filtered read ---------------------------------------------------------------------------


class ImpactCiOut(BaseModel):
    ref: str
    ci_id: int
    number: str
    name: str
    class_key: str
    class_name: str
    incident_state: str | None
    incident_functionality: str | None
    #: linked to the change (True) or reached through propagation (False)
    direct: bool


class ImpactServiceOut(BaseModel):
    service_id: int
    name: str
    #: ``operational|warning|incident``; ``None`` when no CI decides it
    incident_functionality: str | None
    direct: bool


class ImpactIncidentOut(BaseModel):
    ticket_id: int
    tn: str
    title: str
    queue_id: int
    state: str
    service_id: int | None


class ChangeImpactOut(BaseModel):
    computed_at: UtcDateTime | None
    requested_at: UtcDateTime | None
    #: nothing computed yet, or a request is newer than the stored set
    stale: bool
    truncated: bool
    cis: list[ImpactCiOut]
    #: CIs of the set the caller may not read, a number only
    hidden_ci_count: int
    #: services reached only through hidden CIs, a number only
    hidden_service_count: int
    services: list[ImpactServiceOut]
    #: open incidents on the services that the caller may read (at most 50, newest first)
    incidents: list[ImpactIncidentOut]
    incidents_truncated: bool


def impact_is_current(row: TiqoraItsmChange) -> bool:
    """The ``impact_computed`` guard: computed, and not before the last request."""
    if row.impact_computed_at is None:
        return False
    return row.impact_requested_at is None or row.impact_computed_at >= row.impact_requested_at


def _functionality(state: CatalogValue | None) -> str | None:
    return None if state is None else state.functionality


async def _service_names(session: AsyncSession, ids: list[int]) -> dict[int, str]:
    out: dict[int, str] = {}
    stmt = text("SELECT id, name FROM service WHERE id IN :ids").bindparams(
        bindparam("ids", expanding=True)
    )
    for start in range(0, len(ids), CHUNK):
        rows = await session.execute(stmt, {"ids": ids[start : start + CHUNK]})
        out.update({int(i): str(n) for i, n in rows.all()})
    return out


async def open_incident_ids(
    session: AsyncSession,
    type_names: Sequence[str],
    service_ids: Sequence[int],
    *,
    since: datetime | None = None,
    cap: int = INCIDENT_SCAN_CAP,
) -> list[int]:
    """Newest open tickets of the given types on the services (ids only, unfiltered), at most
    ``cap``. ``since`` keeps tickets created at or after it. Callers filter by the viewer."""
    if not service_ids or not type_names:
        return []
    types = (
        await session.execute(
            text("SELECT id FROM ticket_type WHERE name IN :n").bindparams(
                bindparam("n", expanding=True)
            ),
            {"n": list(type_names)},
        )
    ).all()
    states = (
        await session.execute(
            text(
                "SELECT s.id FROM ticket_state s JOIN ticket_state_type t ON t.id = s.type_id"
                " WHERE t.name IN :n"
            ).bindparams(bindparam("n", expanding=True)),
            {"n": sorted(OPEN_STATE_TYPES)},
        )
    ).all()
    if not types or not states:
        return []
    sql = (
        "SELECT id FROM ticket WHERE service_id IN :s AND type_id IN :ty AND ticket_state_id IN :st"
    )
    if since is not None:
        sql += " AND create_time >= :since"
    stmt = text(sql + " ORDER BY id DESC LIMIT :cap").bindparams(
        bindparam("s", expanding=True),
        bindparam("ty", expanding=True),
        bindparam("st", expanding=True),
    )
    found: list[int] = []
    ids = list(service_ids)
    for start in range(0, len(ids), CHUNK):
        params: dict[str, Any] = {
            "s": ids[start : start + CHUNK],
            "ty": [int(r[0]) for r in types],
            "st": [int(r[0]) for r in states],
            "cap": cap,
        }
        if since is not None:
            params["since"] = since
        rows = await session.execute(stmt, params)
        found.extend(int(r[0]) for r in rows)
    return sorted(set(found), reverse=True)[:cap]


async def _open_incident_ids(
    session: AsyncSession, settings: Settings, service_ids: list[int]
) -> list[int]:
    """Newest open incident tickets (ids only, unfiltered) on the services, at most the scan cap."""
    if not service_ids:
        return []
    itsm_cfg = await load_itsm_settings(session, settings)
    return await open_incident_ids(
        session, [itsm_cfg.incident_type, itsm_cfg.major_incident_type], service_ids
    )


async def read_impact(
    session: AsyncSession,
    settings: Settings,
    *,
    ticket_id: int,
    user_id: int,
    sysconfig: SysConfig,
) -> ChangeImpactOut:
    """The stored impact set as the caller may see it.

    CIs go through the Mode B provider (``get_many`` omits the ones of unreadable classes; they
    are counted). Incident tickets are filtered by ``ro`` on their queue. Services are names and
    states only.
    """
    await require_change_mode(session, settings)
    await _ticket(session, ticket_id, user_id=user_id)
    row = await _row(session, ticket_id)
    stored = (
        await session.execute(
            select(
                TiqoraItsmChangeImpact.kind,
                TiqoraItsmChangeImpact.ref_key,
                TiqoraItsmChangeImpact.direct,
            ).where(TiqoraItsmChangeImpact.ticket_id == ticket_id)
        )
    ).all()
    ci_direct = {int(k): bool(d) for kind, k, d in stored if kind == "ci" and str(k).isdigit()}
    svc_direct = {
        int(k): bool(d) for kind, k, d in stored if kind == "service" and str(k).isdigit()
    }

    provider = get_ci_provider("tiqora", sysconfig)
    if provider is None:
        raise ChangeError("cmdb_unavailable", 409)
    summaries = await provider.get_many(
        session, [CiRef("tiqora", i) for i in sorted(ci_direct)], user_id=user_id
    )
    cis = [
        ImpactCiOut(
            ref=str(ref),
            ci_id=ref.id,
            number=s.number,
            name=s.name,
            class_key=s.class_key,
            class_name=s.class_name,
            incident_state=None if s.incident_state is None else s.incident_state.name,
            incident_functionality=(
                None if s.incident_state is None else s.incident_state.functionality
            ),
            direct=ci_direct[ref.id],
        )
        for ref, s in sorted(summaries.items(), key=lambda kv: kv[0].id)
    ]

    service_ids = sorted(svc_direct)
    names = await _service_names(session, service_ids)
    states = await service_incident_states(session, service_ids)
    # services reached only through CIs the caller cannot read stay hidden (like cmdb.affected)
    via_visible = await services_reached_from(session, [ref.id for ref in summaries])
    shown_ids = [sid for sid in service_ids if svc_direct[sid] or sid in via_visible]
    hidden_services = sum(1 for sid in service_ids if sid in names and sid not in shown_ids)
    services = sorted(
        (
            ImpactServiceOut(
                service_id=sid,
                name=names.get(sid, str(sid)),
                incident_functionality=_functionality(states[sid].state),
                direct=svc_direct[sid],
            )
            for sid in shown_ids
            if sid in names
        ),
        key=lambda s: (not s.direct, s.name.casefold(), s.service_id),
    )

    facts = await readable_tickets(
        session, await _open_incident_ids(session, settings, shown_ids), user_id=user_id
    )
    newest = sorted(facts.values(), key=lambda f: f.ticket_id, reverse=True)
    incidents = [
        ImpactIncidentOut(
            ticket_id=f.ticket_id,
            tn=f.tn,
            title=f.title,
            queue_id=f.queue_id,
            state=f.state,
            service_id=f.service_id,
        )
        for f in newest[:INCIDENT_LIST_CAP]
    ]
    return ChangeImpactOut(
        computed_at=row.impact_computed_at,
        requested_at=row.impact_requested_at,
        stale=not impact_is_current(row),
        truncated=bool(row.impact_truncated),
        cis=cis,
        hidden_ci_count=len(ci_direct) - len(summaries),
        hidden_service_count=hidden_services,
        services=services,
        incidents=incidents,
        incidents_truncated=len(newest) > INCIDENT_LIST_CAP,
    )


__all__ = [
    "ITSM_CHANGE_IMPACT_JOB",
    "ITSM_CHANGE_IMPACT_KIND",
    "ChangeImpactOut",
    "ChangeImpactPayload",
    "ImpactResult",
    "enqueue_impact",
    "enqueue_impact_if_change",
    "impact_is_current",
    "open_incident_ids",
    "read_impact",
    "recompute_impact",
    "refresh_stale_impacts",
    "run_change_impact_job",
]
