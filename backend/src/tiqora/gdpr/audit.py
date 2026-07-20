"""Write helper for the ``tiqora_gdpr_audit`` table."""

from __future__ import annotations

import json

from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.tiqora.models import TiqoraGdprAudit


async def record_audit(
    session: AsyncSession,
    *,
    action: str,
    target: str,
    actor: str,
    counts: dict[str, int],
    force_parallel: bool = False,
) -> None:
    """Insert one audit row and commit. Never stores anonymized values, only counts."""
    session.add(
        TiqoraGdprAudit(
            action=action,
            target=target,
            actor=actor,
            counts=json.dumps(counts, sort_keys=True),
            force_parallel=force_parallel,
        )
    )
    await session.commit()
