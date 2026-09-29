"""Daily S/MIME customer certificate renew — Znuny ``RenewCustomerSMIMECertificates``.

Port of ``Daemon::SchedulerCronTaskManager::Task###RenewCustomerSMIMECertificates``
(``Maint::SMIME::CustomerCertificate::Renew``, 02:02 daily): for every
address of a stored certificate, the customer backend's current
``UserSMIMECertificate`` is added when it is not stored yet (see
:mod:`tiqora.crypto.customer_fetch`).

Gated by ``daemon.smime_customer_renew.enabled`` — default **on**, like the
Znuny task — and a no-op unless ``SMIME`` and ``SMIME::FetchFromCustomer``
are enabled. Running next to Znuny's own task is harmless: a certificate
already in the shared store is skipped.
"""

from __future__ import annotations

import structlog

from tiqora.config import get_settings
from tiqora.crypto.config import resolve_crypto_config
from tiqora.crypto.customer_fetch import fetch_enabled, renew_customer_certificates
from tiqora.db.engine import get_session_factory
from tiqora.domain.settings_store import KEY_SMIME_CUSTOMER_RENEW_ENABLED, get_setting_bool
from tiqora.znuny.sysconfig import SysConfig

logger = structlog.get_logger(__name__)


async def run_smime_customer_renew_tick() -> dict[str, int]:
    factory = get_session_factory()
    settings = get_settings()
    async with factory() as session:
        if not await get_setting_bool(session, KEY_SMIME_CUSTOMER_RENEW_ENABLED, True):
            return {"enabled": 0}
        config = await resolve_crypto_config(settings, SysConfig(session))
        if not fetch_enabled(config):
            return {"enabled": 1, "fetch_from_customer": 0}
        report = await renew_customer_certificates(session, settings, config)
    logger.info("smime_customer_renew_tick", added=len(report.added), errors=len(report.errors))
    return {
        "enabled": 1,
        "fetch_from_customer": 1,
        "added": len(report.added),
        "errors": len(report.errors),
    }
