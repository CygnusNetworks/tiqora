"""Deploy processes / ACLs from Tiqora — ``POST /api/v1/admin/deploy``.

Znuny's "deploy" makes the edited drafts live. Once Znuny is stopped nobody can press
that button there, so Tiqora does the same DB effect (see
:func:`tiqora.deployed.watcher.deploy_from_tiqora`). While Znuny still runs this
stays refused, because Znuny's own deployed config file would go stale.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from tiqora.api.deps import AppSettings, DbSession
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.deployed.gate import can_deploy_from_tiqora
from tiqora.deployed.watcher import deploy_from_tiqora

router = APIRouter(prefix="/deploy", tags=["admin:deploy"])

GATE_DETAIL = (
    "Bereitstellung erfolgt in Znuny. Wenn Znuny nicht mehr läuft, in den Einstellungen "
    "„Prozesse und ACLs in Tiqora bereitstellen“ einschalten."
)
BUSY_DETAIL = "Eine andere Bereitstellung läuft gerade. Bitte gleich noch einmal versuchen."


class DeployIn(BaseModel):
    kind: Literal["process", "acl"]


class DeployOut(BaseModel):
    kind: Literal["process", "acl"]
    revision_id: int
    checksum: str
    action: Literal["recorded", "unchanged"]


@router.post("", response_model=DeployOut)
async def post_deploy(
    body: DeployIn, admin: AdminUser, session: DbSession, settings: AppSettings
) -> DeployOut:
    if not await can_deploy_from_tiqora(session, settings):
        raise HTTPException(status.HTTP_409_CONFLICT, detail=GATE_DETAIL)
    # deploy_from_tiqora owns its transaction: end the read transaction first.
    await session.rollback()
    try:
        result = await deploy_from_tiqora(session, body.kind, user_id=admin.id)
    except TimeoutError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=BUSY_DETAIL) from None
    return DeployOut(
        kind=result.kind,
        revision_id=result.revision_id,
        checksum=result.checksum,
        action=result.action,
    )
