"""GDPR retention worker task — feature-flagged, applies configured retention rules.

Gated by the ``gdpr.retention.enabled`` tiqora_settings key (default OFF —
see ``tiqora.domain.settings_store``), on top of the schema-ownership write
gate enforced by ``tiqora.gdpr.gate.require_write_gate``. A disabled flag or
an inactive ownership gate makes this a no-op, so it is safe to schedule
unconditionally.
"""

from __future__ import annotations

import structlog

from tiqora.config import get_settings
from tiqora.db.engine import get_session_factory
from tiqora.domain.settings_store import KEY_GDPR_RETENTION_ENABLED, get_setting_bool
from tiqora.gdpr.gate import GdprRefusedError

logger = structlog.get_logger(__name__)


async def run_gdpr_retention_tick() -> dict[str, int]:
    """One scheduler tick: check the feature flag, then apply retention rules."""
    from tiqora.gdpr.retention import run_retention

    settings = get_settings()
    factory = get_session_factory()

    async with factory() as session:
        enabled = await get_setting_bool(session, KEY_GDPR_RETENTION_ENABLED, False)
    if not enabled:
        logger.debug("gdpr_retention_disabled")
        return {"enabled": 0}

    try:
        result = await run_retention(factory, settings, actor="worker")
    except GdprRefusedError:
        logger.warning("gdpr_retention_refused_ownership_inactive")
        return {"enabled": 1, "refused": 1}

    logger.info(
        "gdpr_retention_tick",
        rules_applied=result.rules_applied,
        tickets_anonymized=result.tickets_anonymized,
        articles_anonymized=result.articles_anonymized,
    )
    return {
        "enabled": 1,
        "rules_applied": result.rules_applied,
        "tickets_anonymized": result.tickets_anonymized,
        "articles_anonymized": result.articles_anonymized,
    }
