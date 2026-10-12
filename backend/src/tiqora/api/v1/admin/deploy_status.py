"""Deploy status and the admin process list.

``GET /api/v1/admin/deploy-status`` tells admins whether drafts (Znuny's
``pm_entity_sync`` / ``acl_sync`` rows) are waiting for a deploy and which revision
is live. ``GET /api/v1/admin/processes`` lists every process of the active source,
including inactive ones (the agent/portal lists show startable processes only).
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from tiqora.api.deps import AppSettings, DbSession
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.deployed.gate import deploy_mode
from tiqora.deployed.store import RevisionInfo, RevisionKind, latest_revision_info
from tiqora.deployed.sync_tables import pending_sync_count
from tiqora.deployed.znuny_sync import DeployMode, ZnunySyncOut, read_znuny_sync_status
from tiqora.domain.ownership import get_ownership_state
from tiqora.domain.schemas import UtcDateTime
from tiqora.jobs.service import utc_now
from tiqora.process.graph import ProcessRepository

router = APIRouter(tags=["admin:deploy"])


class RevisionOut(BaseModel):
    id: int
    source: str
    entity_count: int
    deployed_at: UtcDateTime
    deployed_by: int | None


class DeployKindStatusOut(BaseModel):
    revision: RevisionOut | None
    pending_changes: int
    running_live: bool  # no revision yet: the engine reads the live rows
    znuny_sync: ZnunySyncOut
    deploy_mode: DeployMode | None


class DeployStatusOut(BaseModel):
    process: DeployKindStatusOut
    acl: DeployKindStatusOut
    ownership_active: bool
    can_deploy_from_tiqora: bool


class AdminProcessOut(BaseModel):
    entity_id: str
    name: str
    state_entity_id: str
    state_name: str  # Active | Inactive | FadeAway | ""
    activity_count: int
    start_interfaces: list[str]  # Interface list of the start dialog


def _revision_out(info: RevisionInfo | None) -> RevisionOut | None:
    if info is None:
        return None
    return RevisionOut(
        id=info.id,
        source=info.source,
        entity_count=info.entity_count,
        deployed_at=info.deployed_at,
        deployed_by=info.deployed_by,
    )


async def _kind_status(
    session: DbSession, settings: AppSettings, kind: RevisionKind
) -> DeployKindStatusOut:
    info = await latest_revision_info(session, kind)
    mode = await deploy_mode(session, settings, kind)
    return DeployKindStatusOut(
        revision=_revision_out(info),
        pending_changes=await pending_sync_count(session, kind),
        running_live=info is None,
        znuny_sync=await read_znuny_sync_status(
            session, kind, newest=info, mode=mode, now=utc_now()
        ),
        deploy_mode=mode,
    )


@router.get("/deploy-status", response_model=DeployStatusOut)
async def get_deploy_status(
    _admin: AdminUser, session: DbSession, settings: AppSettings
) -> DeployStatusOut:
    return DeployStatusOut(
        process=await _kind_status(session, settings, "process"),
        acl=await _kind_status(session, settings, "acl"),
        ownership_active=(await get_ownership_state(session, settings)).active,
        can_deploy_from_tiqora=(await deploy_mode(session, settings, "process")) is not None,
    )


@router.get("/processes", response_model=list[AdminProcessOut])
async def list_admin_processes(_admin: AdminUser, session: DbSession) -> list[AdminProcessOut]:
    # One repository (one source/revision) for the whole request.
    repo = ProcessRepository(session)
    out: list[AdminProcessOut] = []
    for summary in await repo.list_processes():
        graph = await repo.get_process(summary.entity_id)
        activity_count = 0
        interfaces: list[str] = []
        if graph is not None:
            activity_count = len(graph.activities)
            start_dialog_id = graph.config.start_activity_dialog
            if start_dialog_id:
                dialog = await repo.get_activity_dialog(start_dialog_id)
                if dialog is not None:
                    interfaces = list(dialog.config.interface)
        out.append(
            AdminProcessOut(
                entity_id=summary.entity_id,
                name=summary.name,
                state_entity_id=summary.state_entity_id,
                state_name=summary.state_name,
                activity_count=activity_count,
                start_interfaces=interfaces,
            )
        )
    return out
