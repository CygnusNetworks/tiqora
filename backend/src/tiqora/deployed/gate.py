"""The one predicate that decides whether Tiqora may deploy processes / ACLs itself."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.config import Settings
from tiqora.deployed.settings import PROCESS_DEPLOY_SECTION, ProcessDeploySettings
from tiqora.domain.ownership import get_ownership_state
from tiqora.settings_sections.store import load_section_values


async def can_deploy_from_tiqora(session: AsyncSession, settings: Settings) -> bool:
    """Ownership is active (env flag and DB marker) OR the admin switched
    ``process.deploy_from_tiqora`` on. Used by ``POST /admin/deploy`` and
    ``GET /admin/deploy-status`` so both always agree."""
    if (await get_ownership_state(session, settings)).active:
        return True
    values = ProcessDeploySettings.model_validate(
        (
            await load_section_values(
                session, PROCESS_DEPLOY_SECTION, secret_key=settings.secret_key
            )
        ).model_dump()
    )
    return values.deploy_from_tiqora
