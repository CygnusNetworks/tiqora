"""Deploy processes / ACLs from Tiqora — ``POST /api/v1/admin/deploy``.

Znuny's "deploy" makes the edited drafts live. Once Znuny is stopped nobody can press
that button there, so Tiqora does the same DB effect (``local`` mode, see
:func:`tiqora.deployed.watcher.deploy_from_tiqora`). While Znuny still runs, the deploy
is allowed only through the add-on TiqoraSync >= 1.2.0 (``signal`` mode), which runs
Znuny's own dump + purge; otherwise it is refused, because Znuny's own deployed config
file would go stale.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from tiqora.api.deps import AppSettings, DbSession
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.deployed.gate import deploy_mode
from tiqora.deployed.watcher import deploy_from_tiqora
from tiqora.deployed.znuny_sync import AddonInfo, DeployMode, read_addon_info

router = APIRouter(prefix="/deploy", tags=["admin:deploy"])

GATE_DETAIL = (
    "Bereitstellen aus Tiqora braucht das Znuny-Add-on TiqoraSync ab Version 1.2.0 "
    "(zuletzt gesehen: {seen}) oder die Einstellung „Prozesse und ACLs in Tiqora "
    "bereitstellen (Znuny läuft nicht mehr)“."
)
BUSY_DETAIL = "Eine andere Bereitstellung läuft gerade. Bitte gleich noch einmal versuchen."


def _gate_detail(addon: AddonInfo | None) -> str:
    if addon is None:
        return GATE_DETAIL.format(seen="nie")
    seen = addon.seen_at.strftime("%d.%m.%Y %H:%M UTC")
    return GATE_DETAIL.format(seen=f"Version {addon.version or '?'}, {seen}")


class DeployIn(BaseModel):
    kind: Literal["process", "acl"]


class DeployOut(BaseModel):
    kind: Literal["process", "acl"]
    revision_id: int
    checksum: str
    action: Literal["recorded", "unchanged"]
    mode: DeployMode


@router.post("", response_model=DeployOut)
async def post_deploy(
    body: DeployIn, admin: AdminUser, session: DbSession, settings: AppSettings
) -> DeployOut:
    mode = await deploy_mode(session, settings, body.kind)
    if mode is None:
        addon = await read_addon_info(session)
        raise HTTPException(status.HTTP_409_CONFLICT, detail=_gate_detail(addon))
    # deploy_from_tiqora owns its transaction: end the read transaction first.
    await session.rollback()
    try:
        result = await deploy_from_tiqora(session, body.kind, user_id=admin.id, mode=mode)
    except TimeoutError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=BUSY_DETAIL) from None
    return DeployOut(
        kind=result.kind,
        revision_id=result.revision_id,
        checksum=result.checksum,
        action=result.action,
        mode=mode,
    )
