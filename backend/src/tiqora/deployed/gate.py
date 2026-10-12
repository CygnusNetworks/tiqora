"""The one predicate that decides whether and how Tiqora may deploy processes / ACLs itself."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.config import Settings
from tiqora.deployed.settings import PROCESS_DEPLOY_SECTION, ProcessDeploySettings
from tiqora.deployed.store import RevisionKind
from tiqora.deployed.znuny_sync import DeployMode, addon_can_deploy
from tiqora.domain.ownership import get_ownership_state
from tiqora.jobs.service import utc_now
from tiqora.settings_sections.store import load_section_values


async def deploy_mode(
    session: AsyncSession, settings: Settings, kind: RevisionKind
) -> DeployMode | None:
    """How a deploy of ``kind`` runs, or ``None`` when it is refused.

    ``local``: ownership is active (env flag and DB marker). ``signal``: a fresh heartbeat of a
    TiqoraSync with the ``<kind>_deploy`` capability proves the Znuny daemon is alive, so Znuny
    deploys. ``local`` again when the admin switched ``process.deploy_from_tiqora`` on (Znuny no
    longer runs). Used by ``POST /admin/deploy`` and ``GET /admin/deploy-status`` so both always
    agree.
    """
    if (await get_ownership_state(session, settings)).active:
        return "local"
    if await addon_can_deploy(session, kind, now=utc_now()):
        return "signal"
    values = ProcessDeploySettings.model_validate(
        (
            await load_section_values(
                session, PROCESS_DEPLOY_SECTION, secret_key=settings.secret_key
            )
        ).model_dump()
    )
    return "local" if values.deploy_from_tiqora else None
