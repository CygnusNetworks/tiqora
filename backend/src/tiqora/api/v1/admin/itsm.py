"""ITSM mode administration — ``/api/v1/admin/itsm``.

The ITSM settings section is edited through the generic settings-sections API
(``/api/v1/admin/settings-sections/itsm``).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from tiqora.api.deps import AppSettings, DbSession
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.api.v1.admin.itsm_schemas import (
    ItsmAdminOut,
    ItsmDetectedOut,
    ItsmModeIn,
    ItsmSwitchOut,
    LinkTypeCreateIn,
    LinkTypeListOut,
    LinkTypeOut,
    LinkTypePatchIn,
    LinkTypeSyncOut,
    MatrixIn,
    SetupApplyIn,
    SetupItemOut,
    SetupOut,
)
from tiqora.api.v1.itsm_schemas import MatrixOut, ServiceItsmOut, matrix_out, service_out
from tiqora.itsm.ci_provider import CiReadOnly, CiUnavailable
from tiqora.itsm.link_types import (
    LinkTypeError,
    ZnunyLinkType,
    create_link_type,
    delete_link_type,
    list_link_types,
    sync_link_type_rows,
    update_link_type,
    znuny_link_types,
)
from tiqora.itsm.mode import (
    ItsmModeState,
    ModeTransitionNotAllowed,
    allowed_transitions,
    get_itsm_mode,
    get_switch_info,
    set_itsm_mode,
)
from tiqora.itsm.models import TiqoraItsmLinkType
from tiqora.itsm.priority import InvalidMatrix, MatrixCell, load_matrix, save_matrix
from tiqora.itsm.providers import tiqora_cmdb_empty
from tiqora.itsm.services import ServiceMetaIn, ServiceNotFound, save_service_meta, service_info
from tiqora.itsm.settings import load_itsm_settings
from tiqora.itsm.setup import SetupItem, apply_setup, plan_setup
from tiqora.znuny.sysconfig import SysConfig

router = APIRouter(prefix="/itsm", tags=["admin:itsm"])

_MODE_ORDER = ("off", "znuny_readthrough", "tiqora")


def _int(raw: object) -> int | None:
    return raw if isinstance(raw, int) and not isinstance(raw, bool) else None


async def _out(session: DbSession, state: ItsmModeState) -> ItsmAdminOut:
    switch = await get_switch_info(session)
    switched = switch is not None
    empty = await tiqora_cmdb_empty(session)
    allowed = allowed_transitions(state, tiqora_cmdb_empty=empty, switched=switched)
    info = state.detected
    return ItsmAdminOut(
        mode=state.mode,
        source=state.source,
        stored=state.stored,
        changed_at=state.changed_at,
        changed_by=state.changed_by,
        import_run_id=state.import_run_id,
        previous_mode=state.previous_mode,
        switched=switched,
        switch=None
        if switch is None
        else ItsmSwitchOut(
            switched_at=switch.get("switched_at")
            if isinstance(switch.get("switched_at"), str)
            else None,
            by=_int(switch.get("by")),
            run_id=_int(switch.get("run_id")),
        ),
        detected=ItsmDetectedOut(
            packages=dict(info.packages),
            core_installed=info.core_installed,
            cmdb_installed=info.cmdb_installed,
            change_installed=info.change_installed,
            profile=info.profile,
            unsupported_reason=info.unsupported_reason,
        ),
        cmdb_empty=empty,
        allowed_transitions=[m for m in _MODE_ORDER if m in allowed],
    )


@router.get("", response_model=ItsmAdminOut)
async def get_itsm_admin(admin: AdminUser, session: DbSession) -> ItsmAdminOut:
    _ = admin
    return await _out(session, await get_itsm_mode(session, fresh=True))


@router.put("/mode", response_model=ItsmAdminOut)
async def put_itsm_mode(body: ItsmModeIn, admin: AdminUser, session: DbSession) -> ItsmAdminOut:
    try:
        state = await set_itsm_mode(session, body.mode, user_id=admin.id)
    except ModeTransitionNotAllowed as exc:
        await session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "mode_transition_not_allowed",
                "from": exc.current,
                "to": exc.new,
                "reason": exc.reason,
            },
        ) from exc
    out = await _out(session, state)
    await session.commit()
    return out


# --- link types (R2: itsm1 owns the one link-type editor) ---------------------------------

_CONFLICT_CODES = frozenset({"link_type_exists", "link_type_in_use"})


def _link_type_http(exc: LinkTypeError) -> HTTPException:
    code = status.HTTP_409_CONFLICT if exc.code in _CONFLICT_CODES else 422
    return HTTPException(code, detail={"code": exc.code, "message": str(exc)})


def _link_type_out(row: TiqoraItsmLinkType, znuny: ZnunyLinkType | None) -> LinkTypeOut:
    return LinkTypeOut(
        id=row.id,
        name=row.name,
        origin="znuny" if row.origin == "znuny" else "tiqora",
        source_label=row.source_label,
        target_label=row.target_label,
        pointed=znuny.pointed if znuny and row.origin == "znuny" else row.pointed,
        propagation=row.propagation,  # type: ignore[arg-type]
        offer_ticket_ticket=row.offer_ticket_ticket,
        offer_ticket_ci=row.offer_ticket_ci,
        offer_ci_ci=row.offer_ci_ci,
        offer_ci_service=row.offer_ci_service,
        position=row.position,
        valid=row.valid,
        znuny_source_name=znuny.source_name if znuny else None,
        znuny_target_name=znuny.target_name if znuny else None,
    )


async def _get_row(session: DbSession, link_type_id: int) -> TiqoraItsmLinkType:
    row = (
        await session.execute(
            select(TiqoraItsmLinkType).where(TiqoraItsmLinkType.id == link_type_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Link type not found")
    return row


@router.get("/link-types", response_model=LinkTypeListOut)
async def get_link_types(admin: AdminUser, session: DbSession) -> LinkTypeListOut:
    """Read-only: rows plus the Znuny types that have no row yet."""
    _ = admin
    catalogue = await znuny_link_types(SysConfig(session))
    rows = await list_link_types(session)
    return LinkTypeListOut(
        items=[_link_type_out(r, catalogue.get(r.name)) for r in rows],
        missing_from_table=sorted(set(catalogue) - {r.name for r in rows}),
    )


@router.post("/link-types/sync", response_model=LinkTypeSyncOut)
async def post_link_types_sync(admin: AdminUser, session: DbSession) -> LinkTypeSyncOut:
    """ "Aus Znuny aktualisieren": the only way the table changes besides the editor."""
    changed = await sync_link_type_rows(session, user_id=admin.id)
    await session.commit()
    return LinkTypeSyncOut(changed=changed)


@router.post("/link-types", response_model=LinkTypeOut, status_code=status.HTTP_201_CREATED)
async def post_link_type(
    body: LinkTypeCreateIn, admin: AdminUser, session: DbSession
) -> LinkTypeOut:
    fields = body.model_dump()
    offers = {k: fields.pop(k) for k in list(fields) if k.startswith("offer_")}
    try:
        row = await create_link_type(session, offers=offers, user_id=admin.id, **fields)
    except LinkTypeError as exc:
        await session.rollback()
        raise _link_type_http(exc) from exc
    out = _link_type_out(row, None)
    await session.commit()
    return out


@router.patch("/link-types/{link_type_id}", response_model=LinkTypeOut)
async def patch_link_type(
    link_type_id: int, body: LinkTypePatchIn, admin: AdminUser, session: DbSession
) -> LinkTypeOut:
    row = await _get_row(session, link_type_id)
    changes = body.model_dump(exclude_unset=True)
    try:
        await update_link_type(session, row, changes, user_id=admin.id)
    except LinkTypeError as exc:
        await session.rollback()
        raise _link_type_http(exc) from exc
    catalogue = await znuny_link_types(SysConfig(session))
    out = _link_type_out(row, catalogue.get(row.name))
    await session.commit()
    return out


@router.delete("/link-types/{link_type_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_link_type_route(link_type_id: int, admin: AdminUser, session: DbSession) -> None:
    _ = admin
    row = await _get_row(session, link_type_id)
    try:
        await delete_link_type(session, row)
    except LinkTypeError as exc:
        await session.rollback()
        raise _link_type_http(exc) from exc
    await session.commit()


# --- setup (types, workaround state, dynamic fields) ---------------------------------------


def _setup_out(items: list[SetupItem]) -> SetupOut:
    return SetupOut(
        items=[
            SetupItemOut(group=i.group, kind=i.kind, name=i.name, status=i.status, detail=i.detail)
            for i in items
        ],
        complete=all(i.status == "present" for i in items if i.group == "incident_problem"),
    )


@router.get("/setup", response_model=SetupOut)
async def get_setup(admin: AdminUser, session: DbSession, settings: AppSettings) -> SetupOut:
    """Preview: what exists and what "Einrichten" would create. Writes nothing."""
    _ = admin
    return _setup_out(await plan_setup(session, await load_itsm_settings(session, settings)))


@router.post("/setup", response_model=SetupOut)
async def post_setup(
    body: SetupApplyIn, admin: AdminUser, session: DbSession, settings: AppSettings
) -> SetupOut:
    """Create the missing rows of the selected groups; existing rows are never changed."""
    cfg = await load_itsm_settings(session, settings)
    items = await apply_setup(session, cfg, groups=set(body.groups), user_id=admin.id)
    out = _setup_out(items)
    await session.commit()
    return out


@router.get("/priority-matrix", response_model=MatrixOut)
async def get_priority_matrix(
    _admin: AdminUser, session: DbSession, settings: AppSettings
) -> MatrixOut:
    """The matrix of the current mode; Mode A shows Znuny's ``cip_allocate`` read-only."""
    cfg = await load_itsm_settings(session, settings)
    try:
        view = await load_matrix(session, await get_itsm_mode(session), cfg)
    except CiUnavailable as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"code": exc.code}) from exc
    return matrix_out(view, enabled=cfg.priority_matrix_enabled)


@router.put("/priority-matrix", response_model=MatrixOut)
async def put_priority_matrix(
    body: MatrixIn, admin: AdminUser, session: DbSession, settings: AppSettings
) -> MatrixOut:
    """Replace Tiqora's matrix (409 ``read_only`` in Mode A, 422 for an invalid cell set)."""
    try:
        await save_matrix(
            session,
            [MatrixCell(c.criticality, c.impact, c.priority_id) for c in body.cells],
            user_id=admin.id,
        )
    except CiReadOnly as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"code": "read_only"}) from exc
    except InvalidMatrix as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, detail={"code": exc.reason}
        ) from exc
    cfg = await load_itsm_settings(session, settings)
    view = await load_matrix(session, await get_itsm_mode(session), cfg)
    out = matrix_out(view, enabled=cfg.priority_matrix_enabled)
    await session.commit()
    return out


async def _service_meta_out(
    session: DbSession, settings: AppSettings, service_id: int, user_id: int
) -> ServiceItsmOut:
    cfg = await load_itsm_settings(session, settings)
    try:
        info = await service_info(session, service_id, user_id=user_id)
    except ServiceNotFound as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail={"code": "service_not_found"}
        ) from exc
    except CiUnavailable as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"code": exc.code}) from exc
    return service_out(info, default_minutes=cfg.min_minutes_between_incidents)


@router.get("/services/{service_id}/meta", response_model=ServiceItsmOut)
async def get_service_meta(
    service_id: int, admin: AdminUser, session: DbSession, settings: AppSettings
) -> ServiceItsmOut:
    return await _service_meta_out(session, settings, service_id, admin.id)


@router.put("/services/{service_id}/meta", response_model=ServiceItsmOut)
async def put_service_meta(
    service_id: int,
    body: ServiceMetaIn,
    admin: AdminUser,
    session: DbSession,
    settings: AppSettings,
) -> ServiceItsmOut:
    """Replace the Tiqora-side meta (409 ``read_only``: changed Znuny-owned type or criticality)."""
    try:
        await save_service_meta(session, service_id, body, user_id=admin.id)
    except ServiceNotFound as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail={"code": "service_not_found"}
        ) from exc
    except CiReadOnly as exc:  # refused before anything was written
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"code": "read_only"}) from exc
    out = await _service_meta_out(session, settings, service_id, admin.id)
    await session.commit()
    return out
