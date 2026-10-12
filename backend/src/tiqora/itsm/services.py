"""Services: ITSM meta, live incident state and the incident flood warning (design I9).

Mode A (``znuny_readthrough``) reads ``service.type_id`` / ``service.criticality`` and the SLA
``min_time_bet_incidents`` column read-only; the rest of the meta (owner, description, portal
visibility, own flood window) lives in ``tiqora_itsm_service_meta``. Every other mode keeps type
and criticality there too. The incident state of a service comes from the CI provider of the mode.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.itsm.ci_provider import CiReadOnly, CiUnavailable, ServiceIncidentState
from tiqora.itsm.mode import ItsmModeState, get_itsm_mode
from tiqora.itsm.models import TiqoraItsmServiceMeta
from tiqora.itsm.providers import get_ci_provider
from tiqora.itsm.settings import ItsmSettings
from tiqora.itsm.znuny_adapter.reader import get_reader
from tiqora.permissions.engine import PermissionEngine
from tiqora.znuny.sysconfig import SysConfig

#: the newest tickets looked at while searching for a readable recent incident
_RECENT_SCAN: int = 50


class ServiceNotFound(LookupError):
    """No ``service`` row with that id."""


class ServiceMetaIn(BaseModel):
    """Replacement of the Tiqora-side meta of one service (PUT semantics).

    ``service_type`` / ``criticality`` are Znuny's in Mode A: sending a different value is
    refused (``CiReadOnly``); ``None`` means "leave to Znuny" there and "empty" elsewhere.
    """

    service_type: str | None = Field(None, max_length=200)
    criticality: str | None = Field(None, max_length=200)
    owner_user_id: int | None = Field(None, ge=1)
    description: str | None = Field(None, max_length=20000)
    portal_visible: bool = True
    #: None = settings default; 0 = off
    min_minutes_between_incidents: int | None = Field(None, ge=0, le=10080)


@dataclass(frozen=True, slots=True)
class ServiceItsmInfo:
    service_id: int
    name: str
    service_type: str | None
    criticality: str | None
    znuny_owned: bool  # Mode A: type/criticality come from the service columns, read-only
    owner_user_id: int | None
    owner_name: str | None
    description: str | None
    portal_visible: bool
    min_minutes_between_incidents: int | None
    znuny_sla_min_times: Mapping[int, int]  # Mode A: per SLA, read-only display
    incident_state: ServiceIncidentState | None


@dataclass(frozen=True, slots=True)
class RecentIncident:
    ticket_id: int
    tn: str
    title: str
    type_name: str
    created_at: datetime
    minutes_ago: int
    window_minutes: int


def effective_window_minutes(meta_minutes: int | None, settings: ItsmSettings) -> int:
    """The flood window of a service: its own value, else the settings default; 0 = off."""
    return settings.min_minutes_between_incidents if meta_minutes is None else meta_minutes


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def _names(session: AsyncSession, service_ids: Sequence[int]) -> dict[int, str]:
    if not service_ids:
        return {}
    stmt = text("SELECT id, name FROM service WHERE id IN :ids").bindparams(
        bindparam("ids", expanding=True)
    )
    rows = (await session.execute(stmt, {"ids": list(service_ids)})).all()
    return {int(r[0]): str(r[1]) for r in rows}


async def _metas(
    session: AsyncSession, service_ids: Sequence[int]
) -> dict[int, TiqoraItsmServiceMeta]:
    out: dict[int, TiqoraItsmServiceMeta] = {}
    for sid in service_ids:
        row = await session.get(TiqoraItsmServiceMeta, sid)
        if row is not None:
            out[sid] = row
    return out


async def _user_names(session: AsyncSession, user_ids: Sequence[int]) -> dict[int, str]:
    if not user_ids:
        return {}
    stmt = text("SELECT id, first_name, last_name FROM users WHERE id IN :ids").bindparams(
        bindparam("ids", expanding=True)
    )
    rows = (await session.execute(stmt, {"ids": sorted(set(user_ids))})).all()
    return {int(r[0]): f"{r[1]} {r[2]}".strip() for r in rows}


async def _znuny_columns(
    session: AsyncSession, mode: ItsmModeState
) -> tuple[dict[int, tuple[str | None, str | None]], dict[int, int]]:
    """Mode A: service id -> (type name, criticality) and SLA id -> minimum minutes."""
    if mode.mode != "znuny_readthrough":
        return {}, {}
    reader = await get_reader(session)  # CiUnavailable when the Znuny tables are unusable
    services = await reader.service_itsm_columns(session)
    slas = await reader.sla_itsm_columns(session)
    return (
        {sid: (r.type_name, r.criticality) for sid, r in services.items()},
        {sid: r.min_time_bet_incidents for sid, r in slas.items() if r.min_time_bet_incidents},
    )


async def _infos(
    session: AsyncSession, service_ids: Sequence[int], *, with_state: bool, user_id: int | None
) -> dict[int, ServiceItsmInfo]:
    ids = sorted({int(s) for s in service_ids})
    names = await _names(session, ids)
    mode = await get_itsm_mode(session)
    metas = await _metas(session, list(names))
    znuny, sla_times = await _znuny_columns(session, mode)
    owners = await _user_names(
        session, [m.owner_user_id for m in metas.values() if m.owner_user_id is not None]
    )
    states: dict[int, ServiceIncidentState] = {}
    if with_state and names:
        states = await incident_states(session, list(names), mode=mode)
    _ = user_id
    out: dict[int, ServiceItsmInfo] = {}
    for sid, name in names.items():
        meta = metas.get(sid)
        owned = mode.mode == "znuny_readthrough"
        z_type, z_crit = znuny.get(sid, (None, None))
        out[sid] = ServiceItsmInfo(
            service_id=sid,
            name=name,
            service_type=z_type if owned else (meta.service_type if meta else None),
            criticality=z_crit if owned else (meta.criticality if meta else None),
            znuny_owned=owned,
            owner_user_id=meta.owner_user_id if meta else None,
            owner_name=owners.get(meta.owner_user_id) if meta and meta.owner_user_id else None,
            description=meta.description if meta else None,
            portal_visible=meta.portal_visible if meta else True,
            min_minutes_between_incidents=meta.min_minutes_between_incidents if meta else None,
            znuny_sla_min_times=dict(sla_times) if owned else {},
            incident_state=states.get(sid),
        )
    return out


async def incident_states(
    session: AsyncSession, service_ids: Sequence[int], *, mode: ItsmModeState | None = None
) -> dict[int, ServiceIncidentState]:
    """Live incident state per service from the mode's CI provider (empty when none serves)."""
    state = mode or await get_itsm_mode(session)
    provider = get_ci_provider(state.mode, SysConfig(session))
    if provider is None or not service_ids:
        return {}
    try:
        return await provider.service_incident_states(session, list(service_ids))
    except CiUnavailable:
        return {}


async def service_info(session: AsyncSession, service_id: int, *, user_id: int) -> ServiceItsmInfo:
    found = await _infos(session, [service_id], with_state=True, user_id=user_id)
    if service_id not in found:
        raise ServiceNotFound(service_id)
    return found[service_id]


async def service_infos(
    session: AsyncSession, service_ids: Sequence[int]
) -> dict[int, ServiceItsmInfo]:
    """List form: no incident state (that is per-user CI data), unknown ids are absent."""
    return await _infos(session, service_ids, with_state=False, user_id=None)


async def save_service_meta(
    session: AsyncSession,
    service_id: int,
    data: ServiceMetaIn,
    *,
    user_id: int,
    import_mode: bool = False,
) -> None:
    """Replace the meta row. Mode A refuses a type/criticality that differs from Znuny's
    (``CiReadOnly``); ``import_mode`` (the ITSM importer, which switches the mode in the same
    run) bypasses that and stores them. Flushes only."""
    if service_id not in await _names(session, [service_id]):
        raise ServiceNotFound(service_id)
    mode = await get_itsm_mode(session)
    znuny_owned = mode.mode == "znuny_readthrough" and not import_mode
    if znuny_owned:
        columns, _slas = await _znuny_columns(session, mode)
        z_type, z_crit = columns.get(service_id, (None, None))
        if (data.service_type is not None and data.service_type != z_type) or (
            data.criticality is not None and data.criticality != z_crit
        ):
            raise CiReadOnly("service type and criticality belong to Znuny in Mode A")
    row = await session.get(TiqoraItsmServiceMeta, service_id)
    if row is None:
        row = TiqoraItsmServiceMeta(service_id=service_id, change_by=user_id, change_time=_now())
        session.add(row)
    if not znuny_owned:
        row.service_type = data.service_type
        row.criticality = data.criticality
    row.owner_user_id = data.owner_user_id
    row.description = data.description
    row.portal_visible = data.portal_visible
    row.min_minutes_between_incidents = data.min_minutes_between_incidents
    row.change_time = _now()
    row.change_by = user_id
    await session.flush()


async def recent_incident(
    session: AsyncSession,
    *,
    service_id: int,
    exclude_ticket_id: int | None,
    settings: ItsmSettings,
    user_id: int,
) -> RecentIncident | None:
    """Newest incident/major-incident ticket of the service created within the service's window
    that the user may read; ``None`` when the window is off or nothing qualifies."""
    meta = await session.get(TiqoraItsmServiceMeta, service_id)
    window = effective_window_minutes(
        meta.min_minutes_between_incidents if meta else None, settings
    )
    if window <= 0:
        return None
    now = _now()
    stmt = text(
        "SELECT t.id, t.tn, t.title, t.queue_id, t.create_time, tt.name"
        " FROM ticket t JOIN ticket_type tt ON tt.id = t.type_id"
        " WHERE t.service_id = :s AND tt.name IN :types AND t.create_time >= :since"
        " ORDER BY t.create_time DESC, t.id DESC LIMIT :lim"
    ).bindparams(bindparam("types", expanding=True))
    rows = (
        await session.execute(
            stmt,
            {
                "s": service_id,
                "types": [settings.incident_type, settings.major_incident_type],
                "since": now - timedelta(minutes=window),
                "lim": _RECENT_SCAN + (1 if exclude_ticket_id is not None else 0),
            },
        )
    ).all()
    engine = PermissionEngine(session)
    allowed: dict[int, bool] = {}
    for tid, tn, title, queue_id, created, type_name in rows:
        if exclude_ticket_id is not None and int(tid) == exclude_ticket_id:
            continue
        qid = int(queue_id)
        if qid not in allowed:
            allowed[qid] = await engine.check(user_id, qid, "ro")
        if not allowed[qid]:
            continue
        return RecentIncident(
            ticket_id=int(tid),
            tn=str(tn),
            title=str(title),
            type_name=str(type_name),
            created_at=created,
            minutes_ago=max(0, int((now - created).total_seconds() // 60)),
            window_minutes=window,
        )
    return None


__all__ = [
    "RecentIncident",
    "ServiceItsmInfo",
    "ServiceMetaIn",
    "ServiceNotFound",
    "effective_window_minutes",
    "incident_states",
    "recent_incident",
    "save_service_meta",
    "service_info",
    "service_infos",
]
