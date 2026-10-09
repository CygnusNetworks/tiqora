"""Login-page discovery: the enabled SSO providers of one audience (D15)."""

from __future__ import annotations

import contextlib

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.config import Settings
from tiqora.db.tiqora.models import TiqoraIdentityProvider
from tiqora.domain.schemas import LoginProviderOut
from tiqora.identity.env_providers import ensure_env_providers
from tiqora.identity.saml_runtime import saml_availability
from tiqora.identity.store import ProviderRecord, provider_from_row
from tiqora.identity.types import ENV_OIDC_SLUG, UserKind, audience_includes

logger = structlog.get_logger(__name__)


def _login_provider(record: ProviderRecord) -> LoginProviderOut:
    # Env rows without a button label render the frontend's default text (label "").
    label = (record.button_label or "") if record.source == "env" else record.label
    return LoginProviderOut(
        slug=record.slug,
        kind=record.kind,  # type: ignore[arg-type]
        label=label,
        icon=record.button_icon,
    )


async def login_providers(
    session: AsyncSession, settings: Settings, audience: UserKind = "agent"
) -> list[LoginProviderOut]:
    """Enabled SSO providers of *audience* ordered by (sort, id); env OIDC (agents only)
    never depends on a DB row.

    A row that cannot be loaded (e.g. undecryptable secrets) hides only that provider.
    """
    records: list[ProviderRecord] = []
    try:
        await ensure_env_providers(session, settings)
        rows = (
            await session.scalars(
                select(TiqoraIdentityProvider)
                .where(TiqoraIdentityProvider.enabled.is_(True))
                .where(TiqoraIdentityProvider.kind.in_(["oidc", "saml"]))
                .order_by(TiqoraIdentityProvider.sort, TiqoraIdentityProvider.id)
            )
        ).all()
        saml_ok = saml_availability().available
        for row in rows:
            try:
                rec = provider_from_row(row, secret_key=settings.secret_key)
            except Exception as exc:  # noqa: BLE001 - one bad row must not hide the others
                logger.warning("auth_methods_provider_unreadable", slug=row.slug, error=str(exc))
                continue
            if (rec.kind == "saml" and not saml_ok) or not audience_includes(
                rec.audience, audience
            ):
                continue
            records.append(rec)
    except Exception as exc:  # noqa: BLE001 - any DB trouble must not hide password/env login
        logger.warning("auth_methods_providers_failed", error=str(exc))
        records = []
        with contextlib.suppress(Exception):
            await session.rollback()
    entries = [(r.sort, r.id, _login_provider(r)) for r in records if r.slug != ENV_OIDC_SLUG]
    env_row = next((r for r in records if r.slug == ENV_OIDC_SLUG), None)
    if settings.oidc_enabled and audience == "agent":
        if env_row is not None:
            entries.append((env_row.sort, env_row.id, _login_provider(env_row)))
        else:  # synthesised without DB: first
            entries.append(
                (0, -1, LoginProviderOut(slug=ENV_OIDC_SLUG, kind="oidc", label="", icon=None))
            )
    entries.sort(key=lambda e: (e[0], e[1]))
    return [e[2] for e in entries]
