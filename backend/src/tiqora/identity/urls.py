"""Public URLs of the SP side of every provider (D9)."""

from __future__ import annotations

from dataclasses import dataclass

from tiqora.config import Settings
from tiqora.identity.store import ProviderRecord
from tiqora.identity.types import ENV_OIDC_SLUG


class IdentityUrlsUnavailable(Exception):  # noqa: N818 - config state, not an error class
    """Neither ``TIQORA_PUBLIC_BASE_URL`` nor a CORS origin is configured."""


@dataclass(frozen=True, slots=True)
class ProviderUrls:
    agent_redirect_uri: str
    customer_redirect_uri: str
    agent_acs_url: str
    customer_acs_url: str
    sp_entity_id: str
    sp_metadata_url: str
    slo_url: str
    backchannel_logout_url: str
    agent_post_logout_url: str
    customer_post_logout_url: str


def base_url(settings: Settings) -> str:
    """Public base URL without a trailing slash; falls back to the first CORS origin."""
    configured = settings.public_base_url.strip().rstrip("/")
    if configured:
        return configured
    for origin in settings.cors_origin_list:
        if origin.strip() and origin.strip() != "*":
            return origin.strip().rstrip("/")
    raise IdentityUrlsUnavailable("TIQORA_PUBLIC_BASE_URL is not set")


def _empty_urls(redirect_uri: str) -> ProviderUrls:
    return ProviderUrls(
        agent_redirect_uri=redirect_uri,
        customer_redirect_uri="",
        agent_acs_url="",
        customer_acs_url="",
        sp_entity_id="",
        sp_metadata_url="",
        slo_url="",
        backchannel_logout_url="",
        agent_post_logout_url="",
        customer_post_logout_url="",
    )


def provider_urls(settings: Settings, record: ProviderRecord) -> ProviderUrls:
    """All SP URLs of *record*. The env OIDC provider keeps ``TIQORA_OIDC_REDIRECT_URI``.

    Env OIDC needs no base URL as long as its redirect URI is configured, so
    production SSO never depends on ``TIQORA_PUBLIC_BASE_URL``.
    """
    env_redirect = settings.oidc_redirect_uri if record.slug == ENV_OIDC_SLUG else ""
    try:
        base = base_url(settings)
    except IdentityUrlsUnavailable:
        if env_redirect:
            return _empty_urls(env_redirect)
        raise
    slug = record.slug
    metadata = f"{base}/api/v1/auth/saml/{slug}/metadata"
    return ProviderUrls(
        agent_redirect_uri=env_redirect or f"{base}/api/v1/auth/oidc/{slug}/callback",
        customer_redirect_uri=f"{base}/api/portal/auth/oidc/{slug}/callback",
        agent_acs_url=f"{base}/api/v1/auth/saml/{slug}/acs",
        customer_acs_url=f"{base}/api/portal/auth/saml/{slug}/acs",
        sp_entity_id=metadata,
        sp_metadata_url=metadata,
        slo_url=f"{base}/api/v1/auth/saml/{slug}/sls",
        backchannel_logout_url=f"{base}/api/v1/auth/oidc/{slug}/backchannel-logout",
        agent_post_logout_url=f"{base}/login?logged_out=1",
        customer_post_logout_url=f"{base}/portal/login?logged_out=1",
    )
