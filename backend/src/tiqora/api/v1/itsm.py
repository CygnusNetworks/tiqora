"""ITSM endpoints for agents — ``/api/v1/itsm`` (API-key area ``cmdb``, step 0)."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Annotated, Final, Literal, NoReturn

from fastapi import APIRouter, HTTPException, Path, Query, Response, status
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.api.deps import AppSettings, CurrentUser, DbSession
from tiqora.api.v1.itsm_schemas import (
    CatalogOut,
    CatalogValueOut,
    CiAttributeOut,
    CiClassOut,
    CiDetailOut,
    CiPageOut,
    CiSummaryOut,
    CiTicketLinkIn,
    CiTicketLinkOut,
    CiVersionOut,
    CiVersionPageOut,
    CloseIncidentsIn,
    ItsmStatusOut,
    ItsmZnunyInfoOut,
    LinkedArticlesIn,
    LinkGraphEdgeOut,
    LinkGraphNodeOut,
    LinkGraphOut,
    LinkTypeOptionOut,
    MajorBannerOut,
    MajorIncidentDeclareIn,
    MajorIncidentOut,
    MajorIncidentUpdateIn,
    MajorIncidentUpdateOut,
    MajorTimelineEntryOut,
    MatrixOut,
    PrioritySuggestionOut,
    ProblemIn,
    ProblemIncidentsIn,
    ProblemOut,
    ProblemRefOut,
    RecentIncidentOut,
    ServiceIncidentStateOut,
    ServiceItsmOut,
    TicketItsmDialogsOut,
    TicketItsmOut,
    incident_state_out,
    matrix_out,
    service_out,
)
from tiqora.db.engine import get_session_factory
from tiqora.domain.schemas import TicketLinkTargetOut
from tiqora.domain.ticket_write_service import TicketWriteService
from tiqora.itsm.ci_provider import (
    CatalogValue,
    CiAttribute,
    CiError,
    CiNotFound,
    CiPermissionDenied,
    CiProvider,
    CiQuery,
    CiReadOnly,
    CiRef,
    CiSearchTooBroad,
    CiSummary,
    CiUnavailable,
    CiVersionInfo,
)
from tiqora.itsm.link_graph import TicketNotReadable, link_graph
from tiqora.itsm.link_types import LinkPair, offered_link_types
from tiqora.itsm.linked_articles import (
    LinkedArticlesPayload,
    enqueue_linked_articles,
    linked_ticket_ids,
)
from tiqora.itsm.major_incident import (
    MajorIncidentError,
    MajorIncidentView,
    TimelineEntry,
    declare,
    end,
    get_major_incident,
    post_update,
)
from tiqora.itsm.mode import ItsmModeState, get_itsm_mode, require_itsm_enabled
from tiqora.itsm.models import TiqoraItsmMajorIncidentUpdate
from tiqora.itsm.priority import load_matrix, suggest_priority
from tiqora.itsm.problem import (
    ProblemError,
    ProblemView,
    enqueue_close_incidents,
    get_problem,
    link_incidents,
    publish_known_error,
    save_problem,
)
from tiqora.itsm.problem import ProblemIn as ProblemData
from tiqora.itsm.providers import get_ci_provider
from tiqora.itsm.services import (
    ServiceNotFound,
    incident_states,
    recent_incident,
    service_info,
)
from tiqora.itsm.settings import ItsmSettings, load_itsm_settings
from tiqora.itsm.setup import setup_complete
from tiqora.itsm.summary import ticket_itsm_summary
from tiqora.jobs.schemas import JobAccepted
from tiqora.kb.service import KbService
from tiqora.permissions.engine import PermissionEngine
from tiqora.znuny.sysconfig import SysConfig

router = APIRouter(prefix="/itsm", tags=["itsm"])
#: ``/tickets/{id}/cis`` — CIs linked to one ticket (registered next to ``router``).
ticket_router = APIRouter(prefix="/tickets", tags=["itsm"])

#: most tickets one CI page lists (newest first); the CI zoom shows the recent ones
CI_TICKETS_MAX: Final = 200

SourcePath = Annotated[Literal["znuny", "tiqora"], Path(description="CI data source")]


@router.get("/link-types", response_model=list[LinkTypeOptionOut])
async def get_offered_link_types(
    user: CurrentUser,
    session: DbSession,
    pair: Annotated[LinkPair, Query(description="Object pair the link is created for")] = (
        "ticket_ticket"
    ),
) -> list[LinkTypeOptionOut]:
    """The link types the picker offers for *pair* in the current ITSM mode."""
    _ = user
    state = await get_itsm_mode(session)
    if state.mode == "off" and pair != "ticket_ticket":
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"code": "itsm_off"})
    options = await offered_link_types(session, pair=pair, mode=state.mode)
    return [
        LinkTypeOptionOut(
            name=o.name,
            source_label=o.source_label,
            target_label=o.target_label,
            source_name=o.source_name,
            target_name=o.target_name,
            pointed=o.pointed,
            propagation=o.propagation,
            origin=o.origin,
        )
        for o in options
    ]


# --- helpers -----------------------------------------------------------------------------------


def _refuse(code: str, http: int) -> NoReturn:
    raise HTTPException(http, detail={"code": code})


@contextmanager
def _ci_errors() -> Iterator[None]:
    """Provider errors as HTTP: unreadable or unknown CIs are 404 alike (no existence leak)."""
    try:
        yield
    except (CiNotFound, CiPermissionDenied) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "ci_not_found"}) from exc
    except CiUnavailable as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"code": exc.code}) from exc
    except CiReadOnly as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"code": "read_only"}) from exc
    except CiSearchTooBroad as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"code": "search_too_broad"}) from exc
    except CiError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, detail={"code": "link_refused"}
        ) from exc


async def _enabled_provider(session: AsyncSession) -> CiProvider:
    """Gate + provider: 409 ``itsm_off`` while off, ``cmdb_not_installed`` without a provider."""
    state = await require_itsm_enabled(session)
    provider = get_ci_provider(state.mode, SysConfig(session))
    if provider is None:
        _refuse("cmdb_not_installed", status.HTTP_409_CONFLICT)
    return provider


def _parse_ref(source: str, ci_id: int) -> CiRef:
    return CiRef(source, ci_id)  # type: ignore[arg-type]  # source validated by the path type


def _value(v: CatalogValue | None) -> CatalogValueOut | None:
    return (
        None
        if v is None
        else CatalogValueOut(key=v.key, name=v.name, functionality=v.functionality)
    )


def _summary(c: CiSummary) -> CiSummaryOut:
    return CiSummaryOut(
        ref=str(c.ref),
        source=c.ref.source,
        id=c.ref.id,
        number=c.number,
        name=c.name,
        class_key=c.class_key,
        class_name=c.class_name,
        deployment_state=_value(c.deployment_state),
        incident_state=_value(c.incident_state),
        changed_at=c.changed_at,
        edit_url=c.edit_url,
    )


def _attribute(a: CiAttribute) -> CiAttributeOut:
    return CiAttributeOut(
        key=a.key,
        label=a.label,
        kind=a.kind,
        display=a.display,
        raw=a.raw,
        ref=None if a.ref is None else str(a.ref),
        children=[_attribute(c) for c in a.children],
    )


def _version(v: CiVersionInfo) -> CiVersionOut:
    return CiVersionOut(
        id=v.id,
        seq=v.seq,
        name=v.name,
        deployment_state=_value(v.deployment_state),
        incident_state=_value(v.incident_state),
        created_at=v.created_at,
        created_by=v.created_by,
        created_by_name=v.created_by_name,
        change_note=v.change_note,
    )


async def _ticket_queue(session: AsyncSession, ticket_id: int) -> int | None:
    row = (
        await session.execute(text("SELECT queue_id FROM ticket WHERE id = :t"), {"t": ticket_id})
    ).scalar_one_or_none()
    return None if row is None else int(row)


async def _require_ticket(
    session: AsyncSession, user_id: int, ticket_id: int, *, write: bool = False
) -> None:
    """``ro`` on the ticket's queue, else 404 (a ticket never shows whether it exists); with
    ``write`` also ``rw``, else 403."""
    queue_id = await _ticket_queue(session, ticket_id)
    engine = PermissionEngine(session)
    if queue_id is None or not await engine.check(user_id, queue_id, "ro"):
        _refuse("ticket_not_found", status.HTTP_404_NOT_FOUND)
    if write and not await engine.check(user_id, queue_id, "rw"):
        _refuse("forbidden", status.HTTP_403_FORBIDDEN)


async def _readable_tickets(
    session: AsyncSession, user_id: int, ticket_ids: Sequence[int]
) -> dict[int, dict[str, object]]:
    """Ticket facts of the ids the user holds ``ro`` on (others are simply absent)."""
    if not ticket_ids:
        return {}
    stmt = text(
        "SELECT t.id, t.tn, t.title, t.queue_id, ts.name, tst.name"
        " FROM ticket t JOIN ticket_state ts ON ts.id = t.ticket_state_id"
        " JOIN ticket_state_type tst ON tst.id = ts.type_id WHERE t.id IN :ids"
    ).bindparams(bindparam("ids", expanding=True))
    rows = (await session.execute(stmt, {"ids": list(ticket_ids)})).all()
    engine = PermissionEngine(session)
    allowed: dict[int, bool] = {}
    out: dict[int, dict[str, object]] = {}
    for tid, tn, title, queue_id, state, state_type in rows:
        qid = int(queue_id)
        if qid not in allowed:
            allowed[qid] = await engine.check(user_id, qid, "ro")
        if allowed[qid]:
            out[int(tid)] = {
                "tn": tn,
                "title": title,
                "state": state,
                "state_type": state_type,
            }
    return out


async def _status(session: AsyncSession, state: ItsmModeState, cfg: ItsmSettings) -> ItsmStatusOut:
    provider = get_ci_provider(state.mode, SysConfig(session)) if state.mode != "off" else None
    unavailable: str | None = None
    if state.mode != "off" and provider is None:
        unavailable = "cmdb_not_installed"
    elif provider is not None:
        try:
            await provider.is_empty(session)  # CiUnavailable while the data source cannot serve
        except CiUnavailable as exc:
            unavailable, provider = exc.code, None
    info = state.detected
    return ItsmStatusOut(
        mode=state.mode,
        source=state.source,
        ci_available=provider is not None,
        ci_read_only=provider.read_only if provider is not None else True,
        ci_source=provider.source if provider is not None else None,
        unavailable_code=unavailable,
        znuny=ItsmZnunyInfoOut(
            core_installed=info.core_installed,
            cmdb_installed=info.cmdb_installed,
            profile=info.profile,
            packages=dict(info.packages),
        ),
        setup_complete=await setup_complete(session, cfg),
    )


# --- agent routes ------------------------------------------------------------------------------


@router.get("/status", response_model=ItsmStatusOut)
async def get_status(user: CurrentUser, session: DbSession, settings: AppSettings) -> ItsmStatusOut:
    """Mode and CI availability for the UI. Answers while ITSM is off (mode ``off``)."""
    _ = user
    state = await get_itsm_mode(session)
    return await _status(session, state, await load_itsm_settings(session, settings))


@router.get("/priority-matrix", response_model=MatrixOut)
async def get_priority_matrix(
    user: CurrentUser, session: DbSession, settings: AppSettings
) -> MatrixOut:
    """The criticality x impact grid of the current mode (Znuny's in Mode A)."""
    _ = user
    state = await require_itsm_enabled(session)
    cfg = await load_itsm_settings(session, settings)
    with _ci_errors():
        view = await load_matrix(session, state, cfg)
    return matrix_out(view, enabled=cfg.priority_matrix_enabled)


@router.get("/priority-suggestion", response_model=PrioritySuggestionOut)
async def get_priority_suggestion(
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
    criticality: Annotated[str | None, Query(max_length=200)] = None,
    impact: Annotated[str | None, Query(max_length=200)] = None,
) -> PrioritySuggestionOut:
    """What the matrix says for the pair, or ``null`` (unknown pair, matrix disabled)."""
    _ = user
    state = await require_itsm_enabled(session)
    cfg = await load_itsm_settings(session, settings)
    return PrioritySuggestionOut(
        priority_id=await suggest_priority(
            session, state, cfg, criticality=criticality, impact=impact
        )
    )


@router.get("/catalog", response_model=CatalogOut)
async def get_catalog(user: CurrentUser, session: DbSession) -> CatalogOut:
    """Classes the user may read and the deployment / incident states, for the search filters."""
    provider = await _enabled_provider(session)
    with _ci_errors():
        classes = await provider.classes(session, user_id=user.id)
        deployment = await provider.deployment_states(session)
        incident = await provider.incident_states(session)
    return CatalogOut(
        classes=[
            CiClassOut(
                key=c.key, name=c.name, icon=c.icon, can_read=c.can_read, can_write=c.can_write
            )
            for c in classes
        ],
        deployment_states=[v for v in map(_value, deployment) if v is not None],
        incident_states=[v for v in map(_value, incident) if v is not None],
    )


@router.get("/cis", response_model=CiPageOut)
async def search_cis(
    user: CurrentUser,
    session: DbSession,
    q: Annotated[str | None, Query(max_length=200)] = None,
    class_: Annotated[list[str] | None, Query(alias="class", max_length=50)] = None,
    depl: Annotated[list[str] | None, Query(max_length=50)] = None,
    inci: Annotated[list[str] | None, Query(max_length=50)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> CiPageOut:
    """Search CIs by number / name (Mode A also by attribute values) and state filters."""
    provider = await _enabled_provider(session)
    query = CiQuery(
        text=q.strip() or None if q else None,
        class_keys=tuple(class_ or ()),
        deployment_state_keys=tuple(depl or ()),
        incident_state_keys=tuple(inci or ()),
        limit=limit,
        offset=offset,
    )
    with _ci_errors():
        page = await provider.search(session, query, user_id=user.id)
    return CiPageOut(
        items=[_summary(c) for c in page.items], total=page.total, truncated=page.truncated
    )


@router.get("/cis/{source}/{ci_id}", response_model=CiDetailOut)
async def get_ci(
    source: SourcePath,
    ci_id: int,
    user: CurrentUser,
    session: DbSession,
    version_id: Annotated[int | None, Query(ge=1)] = None,
) -> CiDetailOut:
    """One CI with its attributes (the current version unless ``version_id`` asks for another)."""
    provider = await _enabled_provider(session)
    with _ci_errors():
        detail = await provider.get(
            session, _parse_ref(source, ci_id), user_id=user.id, version_id=version_id
        )
    return CiDetailOut(
        summary=_summary(detail.summary),
        version=None if detail.version is None else _version(detail.version),
        attributes=[_attribute(a) for a in detail.attributes],
        definition_version=detail.definition_version,
        read_only=detail.read_only,
    )


@router.get("/cis/{source}/{ci_id}/versions", response_model=CiVersionPageOut)
async def get_ci_versions(
    source: SourcePath,
    ci_id: int,
    user: CurrentUser,
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> CiVersionPageOut:
    """The version history of one CI, newest first."""
    provider = await _enabled_provider(session)
    with _ci_errors():
        rows, total = await provider.versions(
            session, _parse_ref(source, ci_id), user_id=user.id, limit=limit, offset=offset
        )
    return CiVersionPageOut(items=[_version(v) for v in rows], total=total)


@router.get("/cis/{source}/{ci_id}/tickets", response_model=list[TicketLinkTargetOut])
async def get_ci_tickets(
    source: SourcePath, ci_id: int, user: CurrentUser, session: DbSession
) -> list[TicketLinkTargetOut]:
    """Tickets linked to the CI that the user may read (the newest ``CI_TICKETS_MAX`` at most).

    ``source_key`` is the ticket id and ``target_key`` the CI ref; ``other_*`` describe the ticket.
    """
    provider = await _enabled_provider(session)
    ref = _parse_ref(source, ci_id)
    with _ci_errors():
        ticket_ids = await provider.ci_ticket_ids(session, ref, user_id=user.id)
        readable = await _readable_tickets(session, user.id, ticket_ids)
        shown = sorted(readable, reverse=True)[:CI_TICKETS_MAX]
        links = await provider.ticket_links(session, shown, user_id=user.id)
    out: list[TicketLinkTargetOut] = []
    for ticket_id in shown:
        facts = readable[ticket_id]
        for link in links.get(ticket_id, []):
            if link.ci.ref != ref:
                continue
            out.append(
                TicketLinkTargetOut(
                    source_key=str(ticket_id),
                    target_key=str(ref),
                    link_type=link.link_type,
                    state="Valid",
                    other_ticket_id=ticket_id,
                    other_tn=str(facts["tn"]),
                    other_title=str(facts["title"]),
                    other_state=str(facts["state"]),
                    other_state_type=str(facts["state_type"]),
                )
            )
    return out


# --- ticket routes -----------------------------------------------------------------------------


@ticket_router.get("/{ticket_id}/cis", response_model=list[CiTicketLinkOut])
async def list_ticket_cis(
    ticket_id: int, user: CurrentUser, session: DbSession
) -> list[CiTicketLinkOut]:
    """CIs linked to the ticket (those the user may read). Needs ``ro`` on the ticket."""
    provider = await _enabled_provider(session)
    await _require_ticket(session, user.id, ticket_id)
    with _ci_errors():
        links = await provider.ticket_links(session, [ticket_id], user_id=user.id)
    return [
        CiTicketLinkOut(
            ticket_id=ticket_id,
            ci=_summary(lk.ci),
            link_type=lk.link_type,
            direction=lk.direction,
            created_at=lk.created_at,
        )
        for lk in links.get(ticket_id, [])
    ]


@ticket_router.post(
    "/{ticket_id}/cis", status_code=status.HTTP_201_CREATED, response_model=CiTicketLinkOut
)
async def link_ticket_ci(
    ticket_id: int,
    body: CiTicketLinkIn,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> CiTicketLinkOut:
    """Link a CI to the ticket. Needs ``rw`` on the ticket and ``ro`` on the CI. Idempotent."""
    provider = await _enabled_provider(session)
    await _require_ticket(session, user.id, ticket_id, write=True)
    try:
        ref = CiRef.parse(body.ci)
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, detail={"code": "invalid_ci_ref"}
        ) from exc
    link_type = body.link_type or (await load_itsm_settings(session, settings)).ci_ticket_link_type
    with _ci_errors():
        offered = {o.name for o in await provider.ticket_link_types(session)}
        if link_type not in offered:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "link_type_not_offered", "link_type": link_type},
            )
        await provider.link_ticket(
            session, ticket_id=ticket_id, ref=ref, link_type=link_type, user_id=user.id
        )
        links = await provider.ticket_links(session, [ticket_id], user_id=user.id)
    await session.commit()
    created = next(
        (lk for lk in links.get(ticket_id, []) if lk.ci.ref == ref and lk.link_type == link_type),
        None,
    )
    if created is None:  # the CI became unreadable between the two calls; nothing sane to show
        _refuse("ci_not_found", status.HTTP_404_NOT_FOUND)
    return CiTicketLinkOut(
        ticket_id=ticket_id,
        ci=_summary(created.ci),
        link_type=created.link_type,
        direction=created.direction,
        created_at=created.created_at,
    )


@ticket_router.delete("/{ticket_id}/cis/{source}/{ci_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unlink_ticket_ci(
    ticket_id: int,
    source: SourcePath,
    ci_id: int,
    user: CurrentUser,
    session: DbSession,
    link_type: Annotated[
        str | None, Query(max_length=200, description="Only this type (default: every type)")
    ] = None,
) -> Response:
    """Remove the link(s) between the ticket and the CI. Needs ``rw`` on the ticket.

    Idempotent: 204 also when no such link existed.
    """
    provider = await _enabled_provider(session)
    await _require_ticket(session, user.id, ticket_id, write=True)
    with _ci_errors():
        await provider.unlink_ticket(
            session,
            ticket_id=ticket_id,
            ref=_parse_ref(source, ci_id),
            link_type=link_type,
            user_id=user.id,
        )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@ticket_router.post(
    "/{ticket_id}/linked-articles",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobAccepted,
)
async def post_linked_articles(
    ticket_id: int,
    body: LinkedArticlesIn,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> JobAccepted:
    """Queue a note (or e-mail reply) on tickets linked to this one. Needs ``rw`` on the ticket.

    The targets must be linked to the ticket now; the job re-checks links and ``rw`` per target.
    """
    await require_itsm_enabled(session)
    await _require_ticket(session, user.id, ticket_id, write=True)
    cfg = await load_itsm_settings(session, settings)
    targets = list(dict.fromkeys(t for t in body.target_ticket_ids if t != ticket_id))
    if not targets:
        _refuse("no_targets", status.HTTP_422_UNPROCESSABLE_ENTITY)
    if len(targets) > cfg.linked_articles_max_tickets:
        _refuse("too_many_targets", status.HTTP_422_UNPROCESSABLE_ENTITY)
    if not set(targets) <= await linked_ticket_ids(session, ticket_id):
        _refuse("targets_not_linked", status.HTTP_422_UNPROCESSABLE_ENTITY)
    job = await enqueue_linked_articles(
        session,
        LinkedArticlesPayload(
            source_ticket_id=ticket_id,
            target_ticket_ids=targets,
            mode=body.mode,
            subject=body.subject,
            body_html=body.body_html,
            visible_for_customer=body.visible_for_customer,
            origin="linked_note",
            acting_user_id=user.id,
        ),
    )
    await session.commit()
    return JobAccepted(job_id=job.id, status="queued")


# --- problem management (Task 19) -------------------------------------------------------------


@contextmanager
def _problem_errors() -> Iterator[None]:
    try:
        yield
    except ProblemError as exc:
        raise HTTPException(exc.http_status, detail=exc.detail) from exc


def _problem_out(view: ProblemView) -> ProblemOut:
    return ProblemOut.model_validate(view.model_dump())


@ticket_router.get("/{ticket_id}/problem", response_model=ProblemOut)
async def read_problem(
    ticket_id: int, user: CurrentUser, session: DbSession, settings: AppSettings
) -> ProblemOut:
    """Root cause, workaround, known-error state and linked incidents. Needs ``ro``."""
    await require_itsm_enabled(session)
    cfg = await load_itsm_settings(session, settings)
    with _problem_errors():
        return _problem_out(await get_problem(session, ticket_id, user_id=user.id, cfg=cfg))


@ticket_router.put("/{ticket_id}/problem", response_model=ProblemOut)
async def put_problem(
    ticket_id: int, body: ProblemIn, user: CurrentUser, session: DbSession, settings: AppSettings
) -> ProblemOut:
    """Save root cause and workaround. Needs ``rw``; 409 ``not_a_problem`` on other types."""
    await require_itsm_enabled(session)
    cfg = await load_itsm_settings(session, settings)
    with _problem_errors():
        view = await save_problem(
            session, ticket_id, ProblemData(**body.model_dump()), user_id=user.id, cfg=cfg
        )
    await session.commit()
    return _problem_out(view)


@ticket_router.post("/{ticket_id}/problem/incidents", response_model=ProblemOut)
async def post_problem_incidents(
    ticket_id: int,
    body: ProblemIncidentsIn,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> ProblemOut:
    """Link incidents to the problem (``ParentChild``, the problem is the parent); at most 50."""
    await require_itsm_enabled(session)
    cfg = await load_itsm_settings(session, settings)
    with _problem_errors():
        await link_incidents(session, ticket_id, body.incident_ticket_ids, user_id=user.id, cfg=cfg)
        view = await get_problem(session, ticket_id, user_id=user.id, cfg=cfg)
    await session.commit()
    return _problem_out(view)


@ticket_router.post("/{ticket_id}/problem/known-error", response_model=ProblemOut)
async def post_known_error(
    ticket_id: int, user: CurrentUser, session: DbSession, settings: AppSettings
) -> ProblemOut:
    """Publish the problem as a known error: a KB article for review, linked to the ticket."""
    await require_itsm_enabled(session)
    cfg = await load_itsm_settings(session, settings)
    kb = KbService(session, settings)
    try:
        with _problem_errors():
            await publish_known_error(session, kb, ticket_id, user_id=user.id, cfg=cfg)
            view = await get_problem(session, ticket_id, user_id=user.id, cfg=cfg)
    finally:
        await kb.close()
    await session.commit()
    return _problem_out(view)


@ticket_router.post(
    "/{ticket_id}/problem/close-incidents",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobAccepted,
)
async def post_close_incidents(
    ticket_id: int,
    body: CloseIncidentsIn,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> JobAccepted:
    """Queue "close the linked incidents with workaround" (a note and the workaround state)."""
    await require_itsm_enabled(session)
    cfg = await load_itsm_settings(session, settings)
    with _problem_errors():
        job = await enqueue_close_incidents(
            session, ticket_id, user_id=user.id, cfg=cfg, note_html=body.note_html
        )
    await session.commit()
    return JobAccepted(job_id=job.id, status="queued")


# --- major incident (Task 20) -----------------------------------------------------------------


@contextmanager
def _major_errors() -> Iterator[None]:
    try:
        yield
    except MajorIncidentError as exc:
        raise HTTPException(exc.http_status, detail=exc.detail) from exc


def _major_out(view: MajorIncidentView) -> MajorIncidentOut:
    return MajorIncidentOut.model_validate(view.model_dump())


@ticket_router.get("/{ticket_id}/major-incident", response_model=MajorIncidentOut)
async def read_major_incident(
    ticket_id: int, user: CurrentUser, session: DbSession
) -> MajorIncidentOut:
    """The declaration with its timeline. Needs ``ro``; 404 ``no_major_incident`` otherwise."""
    await require_itsm_enabled(session)
    with _major_errors():
        return _major_out(await get_major_incident(session, ticket_id, user_id=user.id))


@ticket_router.post(
    "/{ticket_id}/major-incident",
    response_model=MajorIncidentOut,
    status_code=status.HTTP_200_OK,
)
async def post_major_incident(
    ticket_id: int,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
    body: MajorIncidentDeclareIn | None = None,
) -> MajorIncidentOut:
    """Declare the ticket a major incident (type change + banner). Idempotent. Needs ``rw``."""
    await require_itsm_enabled(session)
    cfg = await load_itsm_settings(session, settings)
    with _major_errors():
        view = await declare(
            session,
            ticket_id,
            summary=body.summary if body else None,
            user_id=user.id,
            sysconfig=SysConfig(session),
            cfg=cfg,
        )
    await session.commit()
    return _major_out(view)


@ticket_router.delete("/{ticket_id}/major-incident", response_model=MajorIncidentOut)
async def delete_major_incident(
    ticket_id: int, user: CurrentUser, session: DbSession
) -> MajorIncidentOut:
    """End the major incident (the ticket keeps its type). Needs ``rw``."""
    await require_itsm_enabled(session)
    with _major_errors():
        view = await end(session, ticket_id, user_id=user.id)
    await session.commit()
    return _major_out(view)


def _update_entry(update: TiqoraItsmMajorIncidentUpdate) -> MajorTimelineEntryOut:
    return MajorTimelineEntryOut.model_validate(
        TimelineEntry(
            kind="update",
            at=update.created_at,
            by=update.created_by,
            update_id=int(update.id),
            mode=update.mode,  # type: ignore[arg-type]
            body=update.body,
            target_count=int(update.target_count),
            job_id=None if update.job_id is None else int(update.job_id),
        ).model_dump()
    )


@ticket_router.post(
    "/{ticket_id}/major-incident/updates",
    response_model=MajorIncidentUpdateOut,
    status_code=status.HTTP_201_CREATED,
)
async def post_major_incident_update(
    ticket_id: int,
    body: MajorIncidentUpdateIn,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> MajorIncidentUpdateOut:
    """Post a status update; ``note``/``reply`` also queue the job for the linked tickets."""
    await require_itsm_enabled(session)
    cfg = await load_itsm_settings(session, settings)
    writer = TicketWriteService(session, get_session_factory(), SysConfig(session))
    with _major_errors():
        update, job = await post_update(
            session,
            writer,
            ticket_id,
            mode=body.mode,
            body_html=body.body_html,
            user_id=user.id,
            cfg=cfg,
        )
    entry = _update_entry(update)
    await session.commit()
    return MajorIncidentUpdateOut(update=entry, job_id=None if job is None else int(job.id))


# --- services (Task 17) ----------------------------------------------------------------------


@router.get("/services/incident-states", response_model=dict[int, ServiceIncidentStateOut])
async def get_service_incident_states(
    user: CurrentUser,
    session: DbSession,
    ids: Annotated[list[int], Query(max_length=200, description="Service ids (at most 200)")],
) -> dict[int, ServiceIncidentStateOut]:
    """Live incident state of up to 200 services (the service picker and list badges)."""
    _ = user
    state = await require_itsm_enabled(session)
    found = await incident_states(session, sorted(set(ids)), mode=state)
    return {sid: incident_state_out(s) for sid, s in found.items()}


@router.get("/services/{service_id}", response_model=ServiceItsmOut)
async def get_service(
    service_id: int, user: CurrentUser, session: DbSession, settings: AppSettings
) -> ServiceItsmOut:
    await require_itsm_enabled(session)
    cfg = await load_itsm_settings(session, settings)
    try:
        info = await service_info(session, service_id, user_id=user.id)
    except ServiceNotFound:
        _refuse("service_not_found", status.HTTP_404_NOT_FOUND)
    except CiUnavailable as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"code": exc.code}) from exc
    return service_out(info, default_minutes=cfg.min_minutes_between_incidents)


@router.get("/services/{service_id}/cis", response_model=list[CiSummaryOut])
async def get_service_cis(
    service_id: int,
    user: CurrentUser,
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[CiSummaryOut]:
    """The configuration items allocated to a service (readable classes only)."""
    provider = await _enabled_provider(session)
    with _ci_errors():
        found = await provider.service_cis(session, service_id, user_id=user.id, limit=limit)
    return [_summary(c) for c in found]


@router.get("/services/{service_id}/recent-incident", response_model=RecentIncidentOut | None)
async def get_recent_incident(
    service_id: int,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
    exclude_ticket_id: Annotated[int | None, Query(ge=1)] = None,
) -> RecentIncidentOut | None:
    """The incident flood warning: a recent incident on the same service (null = none)."""
    await require_itsm_enabled(session)
    cfg = await load_itsm_settings(session, settings)
    found = await recent_incident(
        session,
        service_id=service_id,
        exclude_ticket_id=exclude_ticket_id,
        settings=cfg,
        user_id=user.id,
    )
    if found is None:
        return None
    return RecentIncidentOut(
        ticket_id=found.ticket_id,
        tn=found.tn,
        title=found.title,
        type_name=found.type_name,
        created_at=found.created_at,
        minutes_ago=found.minutes_ago,
        window_minutes=found.window_minutes,
    )


# --- ticket ITSM summary and link graph (Task 21) ------------------------------------------


@ticket_router.get("/{ticket_id}/itsm", response_model=TicketItsmOut)
async def get_ticket_itsm(
    ticket_id: int, user: CurrentUser, session: DbSession, settings: AppSettings
) -> TicketItsmOut:
    """What the ticket zoom shows about the ITSM side. No gate: while ITSM is off or not set up
    the answer is ``enabled=false``. Needs ``ro`` on the ticket (404 otherwise)."""
    cfg = await load_itsm_settings(session, settings)
    try:
        s = await ticket_itsm_summary(session, ticket_id, user_id=user.id, cfg=cfg)
    except TicketNotReadable:
        _refuse("ticket_not_found", status.HTTP_404_NOT_FOUND)
    banner = s.major_banner
    problem = s.problem
    recent = s.recent_incident
    return TicketItsmOut(
        enabled=s.enabled,
        mode=s.mode,
        role=s.role,
        criticality=s.criticality,
        impact=s.impact,
        matrix_priority_id=s.matrix_priority_id,
        service=None
        if s.service is None
        else service_out(s.service, default_minutes=cfg.min_minutes_between_incidents),
        major_incident=None if s.major_incident is None else _major_out(s.major_incident),
        major_banner=None if banner is None else MajorBannerOut.model_validate(banner.model_dump()),
        problem=None
        if problem is None
        else ProblemRefOut(
            ticket_id=problem.ticket_id,
            tn=problem.tn,
            title=problem.title,
            known_error=problem.known_error,
        ),
        ci_count=s.ci_count,
        recent_incident=None
        if recent is None
        else RecentIncidentOut(
            ticket_id=recent.ticket_id,
            tn=recent.tn,
            title=recent.title,
            type_name=recent.type_name,
            created_at=recent.created_at,
            minutes_ago=recent.minutes_ago,
            window_minutes=recent.window_minutes,
        ),
        dialogs=TicketItsmDialogsOut(
            itsm_fields=s.dialogs.itsm_fields, decision=s.dialogs.decision
        ),
        known_error_category_configured=cfg.known_error_kb_category_id > 0,
        major_incident_customer_reply=cfg.major_incident_customer_reply,
    )


@ticket_router.get("/{ticket_id}/link-graph", response_model=LinkGraphOut)
async def get_ticket_link_graph(
    ticket_id: int,
    user: CurrentUser,
    session: DbSession,
    depth: Annotated[int, Query(ge=1, le=3, description="Hops from the ticket")] = 2,
) -> LinkGraphOut:
    """Linked tickets (and CIs while ITSM is on) around the ticket. No gate. Tickets and CIs the
    caller may not read are left out; 404 when the ticket itself is not readable."""
    try:
        graph = await link_graph(session, ticket_id, user_id=user.id, depth=depth)
    except TicketNotReadable:
        _refuse("ticket_not_found", status.HTTP_404_NOT_FOUND)
    return LinkGraphOut(
        root=graph.root,
        nodes=[
            LinkGraphNodeOut(
                key=n.key,
                kind=n.kind,
                depth=n.depth,
                number=n.number,
                title=n.title,
                state=n.state,
                state_type=n.state_type,
                class_name=n.class_name,
                incident_state=n.incident_state,
                incident_functionality=n.incident_functionality,
            )
            for n in graph.nodes
        ],
        edges=[
            LinkGraphEdgeOut(source=e.source, target=e.target, link_type=e.link_type, kind=e.kind)
            for e in graph.edges
        ],
        truncated=graph.truncated,
    )
