"""ITSM mode administration — ``/api/v1/admin/itsm``.

The ITSM settings section is edited through the generic settings-sections API
(``/api/v1/admin/settings-sections/itsm``).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from tiqora.api.deps import DbSession
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.api.v1.admin.itsm_schemas import (
    ItsmAdminOut,
    ItsmDetectedOut,
    ItsmModeIn,
    ItsmSwitchOut,
)
from tiqora.itsm.mode import (
    ItsmModeState,
    ModeTransitionNotAllowed,
    allowed_transitions,
    get_itsm_mode,
    get_switch_info,
    set_itsm_mode,
)
from tiqora.itsm.providers import tiqora_cmdb_empty

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
