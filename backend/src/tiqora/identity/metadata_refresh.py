"""Fetch and refresh SAML IdP metadata from ``idp_metadata_url`` (daily worker tick).

A failed fetch or parse never touches the last good ``metadata_xml`` and never disables the
provider: the German reason is stored in ``metadata_error`` where the admin UI shows it.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.config import Settings, get_settings
from tiqora.db.engine import get_session_factory
from tiqora.db.tiqora.models import TiqoraIdentityProvider
from tiqora.domain.settings_store import KEY_IDENTITY_METADATA_REFRESH_ENABLED, get_setting_bool
from tiqora.identity.config_models import SamlProviderConfig
from tiqora.identity.saml_metadata import SamlMetadataError, fetch_idp_metadata, parse_idp_metadata
from tiqora.identity.store import ProviderRecord, list_providers, provider_from_row

logger = structlog.get_logger(__name__)

_ERROR_MAX = 500  # tiqora_identity_provider.metadata_error is VARCHAR(500)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _saml_config(record: ProviderRecord) -> SamlProviderConfig | None:
    return record.config if isinstance(record.config, SamlProviderConfig) else None


def _is_refreshable(record: ProviderRecord) -> bool:
    """URL-based SAML provider without pasted XML (pasted XML always wins, nothing to fetch)."""
    config = _saml_config(record)
    return (
        config is not None
        and record.source == "db"
        and bool(config.idp_metadata_url.strip())
        and not config.idp_metadata_xml.strip()
    )


async def refresh_provider_metadata(
    session: AsyncSession,
    record: ProviderRecord,
    *,
    settings: Settings,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ProviderRecord:
    """Download + validate the metadata and store it (flushed, not committed).

    Raises ``SamlMetadataError`` on any problem; the stored metadata is left untouched then.
    """
    config = _saml_config(record)
    if config is None or not config.idp_metadata_url.strip():
        raise SamlMetadataError("Für diesen Anmeldedienst ist keine Metadaten-URL hinterlegt.")
    xml = await fetch_idp_metadata(
        config.idp_metadata_url.strip(),
        allow_private_network=config.allow_private_network,
        transport=transport,
    )
    parse_idp_metadata(xml, entity_id=config.idp_entity_id or None)
    row = await session.get(TiqoraIdentityProvider, record.id)
    if row is None:
        raise SamlMetadataError("Der Anmeldedienst existiert nicht mehr.")
    row.metadata_xml = xml
    row.metadata_fetched_at = _utcnow()
    row.metadata_error = None
    await session.flush()
    return provider_from_row(row, secret_key=settings.secret_key)


async def record_metadata_error(session: AsyncSession, provider_id: int, message: str) -> None:
    await session.rollback()
    row = await session.get(TiqoraIdentityProvider, provider_id)
    if row is not None:
        row.metadata_error = message[:_ERROR_MAX]
        await session.commit()


async def run_identity_metadata_refresh_tick(
    *,
    settings: Settings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, int]:
    settings = settings or get_settings()
    counts = {"refreshed": 0, "failed": 0, "skipped": 0}
    async with get_session_factory()() as session:
        if not await get_setting_bool(session, KEY_IDENTITY_METADATA_REFRESH_ENABLED, True):
            return counts
        records = [
            r
            for r in await list_providers(session, secret_key=settings.secret_key)
            if r.kind == "saml"
        ]
        for record in records:
            if not _is_refreshable(record):
                counts["skipped"] += 1
                continue
            try:
                await refresh_provider_metadata(
                    session, record, settings=settings, transport=transport
                )
                await session.commit()
                counts["refreshed"] += 1
            except Exception as exc:
                counts["failed"] += 1
                message = (
                    str(exc)
                    if isinstance(exc, SamlMetadataError)
                    else "Die Metadaten konnten nicht aktualisiert werden."
                )
                logger.warning(
                    "identity_metadata_refresh_failed",
                    provider_id=record.id,
                    slug=record.slug,
                    error=message,
                    exc_info=not isinstance(exc, SamlMetadataError),
                )
                try:
                    await record_metadata_error(session, record.id, message)
                except Exception:
                    logger.exception("identity_metadata_error_not_stored", provider_id=record.id)
                    await session.rollback()
    logger.info("identity_metadata_refresh_tick", **counts)
    return counts


__all__ = [
    "record_metadata_error",
    "refresh_provider_metadata",
    "run_identity_metadata_refresh_tick",
]
