"""Shared ownership gate for GDPR writes to Znuny-owned tables.

Anonymizing PII (or scrubbing article bodies) while Znuny is still the
system of record for those rows can confuse a running Znuny process (stale
caches, in-flight edits racing the scrub, etc.). Both
:mod:`tiqora.gdpr.anonymize` and :mod:`tiqora.gdpr.retention` must refuse to
write unless schema-ownership is active, or the caller passes
``force_parallel=True`` — which is logged loudly, since it is an explicit
acknowledgement of the risk.
"""

from __future__ import annotations

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.config import Settings
from tiqora.domain.ownership import get_ownership_state

logger = structlog.get_logger(__name__)


class GdprRefusedError(RuntimeError):
    """Raised when a GDPR write is refused because ownership is inactive."""


async def require_write_gate(
    session: AsyncSession,
    settings: Settings,
    *,
    force_parallel: bool,
    operation: str,
) -> None:
    """Refuse to proceed unless schema-ownership is active or force_parallel is set.

    On ``force_parallel=True`` this logs a loud warning instead of refusing —
    the caller has explicitly accepted the risk of writing PII changes to
    Znuny-owned tables while Znuny may still be running in parallel.
    """
    state = await get_ownership_state(session, settings)
    if state.active:
        return
    if force_parallel:
        logger.warning(
            "gdpr_force_parallel_write",
            operation=operation,
            warning=(
                "Schema ownership is NOT active but --force-parallel was passed: "
                "writing GDPR changes to Znuny-owned tables while Znuny may still "
                "be running in parallel. This can confuse a running Znuny process "
                "(stale caches, races with in-flight edits). Proceed only if you "
                "understand and accept this risk."
            ),
        )
        return
    raise GdprRefusedError(
        f"Refusing to run '{operation}': schema-ownership gate is not active "
        "(see `tiqora ownership status`). GDPR writes touch Znuny-owned tables "
        "(customer_user, article_data_mime, customer_company) and must not run "
        "during parallel operation without an explicit, understood risk. "
        "Pass --force-parallel to override."
    )
