"""Znuny-compatible OAuth2 for mail accounts (token config + XOAUTH2).

Ports the behavioural core of ``Kernel::System::OAuth2Token`` /
``OAuth2TokenConfig`` so Tiqora can share the same legacy tables and config
JSON with Znuny in parallel operation.

Config JSON keys and request types match Znuny exactly:

* top-level: ``ClientID``, ``ClientSecret``, ``Scope``, ``Requests``, ``Notifications``
* request types: ``AuthorizationCode``, ``TokenByAuthorizationCode``, ``TokenByRefreshToken``
* autofill keys: ``ClientID``, ``ClientSecret``, ``Scope``, ``State``, ``RedirectURL``,
  ``AuthorizationCode``, ``RefreshToken``, ``Token``
* state value: ``TokenConfigID{id}``
"""

from __future__ import annotations

import json
import re
from base64 import b64encode
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.config import Settings, get_settings
from tiqora.db.legacy.oauth2 import OAuth2Token, OAuth2TokenConfig
from tiqora.security.outbound import OutboundURLError, pin_outbound_url

logger = structlog.get_logger(__name__)

VALID_ID_VALID = 1
VALID_ID_INVALID = 2

# Redis-backed anti-CSRF nonce for the public authorization callback.
OAUTH2_STATE_REDIS_PREFIX = "tiqora:oauth2:authstate:"
OAUTH2_STATE_TTL = 600

STATE_PREFIX = "TokenConfigID"
# Optional ``.<nonce>`` suffix carries a per-authorization anti-CSRF token that
# the public callback validates against Redis (security review). The bare
# ``TokenConfigID<n>`` form stays parseable for Znuny compatibility.
STATE_RE = re.compile(r"\ATokenConfigID(\d+)(?:\.([A-Za-z0-9_-]+))?\Z")

REQUEST_AUTH_CODE = "AuthorizationCode"
REQUEST_TOKEN_BY_CODE = "TokenByAuthorizationCode"
REQUEST_TOKEN_BY_REFRESH = "TokenByRefreshToken"

# ---------------------------------------------------------------------------
# Provider templates (from Znuny scripts/OAuth2TokenManagement/TokenConfigTemplates)
# ---------------------------------------------------------------------------

_MS_SCOPE = (
    "https://outlook.office.com/IMAP.AccessAsUser.All "
    "https://outlook.office.com/POP.AccessAsUser.All "
    "https://outlook.office.com/SMTP.Send offline_access"
)

TEMPLATE_MICROSOFT_EXCHANGE_ONLINE: dict[str, Any] = {
    "name": "Microsoft Exchange Online",
    "config": {
        "ClientID": "",
        "ClientSecret": "",
        "Scope": _MS_SCOPE,
        "Requests": {
            "AuthorizationCode": {
                "Request": {
                    "AutofilledParametersMapping": {
                        "client_id": "ClientID",
                        "redirect_uri": "RedirectURL",
                        "scope": "Scope",
                        "state": "State",
                    },
                    "Parameters": {
                        "response_mode": "query",
                        "response_type": "code",
                        "prompt": "select_account",
                    },
                    "URL": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
                },
                "Response": {
                    "ParametersMapping": {
                        "code": "AuthorizationCode",
                        "state": "State",
                    }
                },
            },
            "TokenByAuthorizationCode": {
                "Request": {
                    "AutofilledParametersMapping": {
                        "client_id": "ClientID",
                        "client_secret": "ClientSecret",
                        "code": "AuthorizationCode",
                        "redirect_uri": "RedirectURL",
                        "scope": "Scope",
                    },
                    "Parameters": {"grant_type": "authorization_code"},
                    "URL": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
                },
                "Response": {
                    "ParametersMapping": {
                        "access_token": "Token",
                        "error": "ErrorMessage",
                        "error_codes": "ErrorCode",
                        "error_description": "ErrorDescription",
                        "expires_in": "TokenExpirationDate",
                        "refresh_token": "RefreshToken",
                    }
                },
            },
            "TokenByRefreshToken": {
                "Request": {
                    "AutofilledParametersMapping": {
                        "client_id": "ClientID",
                        "client_secret": "ClientSecret",
                        "refresh_token": "RefreshToken",
                        "scope": "Scope",
                    },
                    "Parameters": {"grant_type": "refresh_token"},
                    "URL": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
                },
                "Response": {
                    "ParametersMapping": {
                        "access_token": "Token",
                        "error": "ErrorMessage",
                        "error_codes": "ErrorCode",
                        "error_description": "ErrorDescription",
                        "expires_in": "TokenExpirationDate",
                        "refresh_token": "RefreshToken",
                    }
                },
            },
        },
        "Notifications": {
            "NotifyOnExpiredToken": 0,
            "NotifyOnExpiredRefreshToken": 1,
        },
    },
}

TEMPLATE_GOOGLE_MAIL: dict[str, Any] = {
    "name": "Google Mail",
    "config": {
        "ClientID": "",
        "ClientSecret": "",
        "Scope": "https://mail.google.com/",
        "Requests": {
            "AuthorizationCode": {
                "Request": {
                    "AutofilledParametersMapping": {
                        "client_id": "ClientID",
                        "redirect_uri": "RedirectURL",
                        "scope": "Scope",
                        "state": "State",
                    },
                    "Parameters": {
                        "access_type": "offline",
                        "response_type": "code",
                        "prompt": "consent",
                    },
                    "URL": "https://accounts.google.com/o/oauth2/v2/auth",
                },
                "Response": {
                    "ParametersMapping": {
                        "code": "AuthorizationCode",
                        "state": "State",
                    }
                },
            },
            "TokenByAuthorizationCode": {
                "Request": {
                    "AutofilledParametersMapping": {
                        "client_id": "ClientID",
                        "client_secret": "ClientSecret",
                        "code": "AuthorizationCode",
                        "redirect_uri": "RedirectURL",
                    },
                    "Parameters": {"grant_type": "authorization_code"},
                    "URL": "https://oauth2.googleapis.com/token",
                },
                "Response": {
                    "ParametersMapping": {
                        "access_token": "Token",
                        "error": "ErrorMessage",
                        "error_description": "ErrorDescription",
                        "expires_in": "TokenExpirationDate",
                        "refresh_token": "RefreshToken",
                    }
                },
            },
            "TokenByRefreshToken": {
                "Request": {
                    "AutofilledParametersMapping": {
                        "client_id": "ClientID",
                        "client_secret": "ClientSecret",
                        "refresh_token": "RefreshToken",
                    },
                    "Parameters": {"grant_type": "refresh_token"},
                    "URL": "https://oauth2.googleapis.com/token",
                },
                "Response": {
                    "ParametersMapping": {
                        "access_token": "Token",
                        "error": "ErrorMessage",
                        "error_description": "ErrorDescription",
                        "expires_in": "TokenExpirationDate",
                    }
                },
            },
        },
        "Notifications": {
            "NotifyOnExpiredToken": 0,
            "NotifyOnExpiredRefreshToken": 1,
        },
    },
}

PROVIDER_TEMPLATES: list[dict[str, Any]] = [
    TEMPLATE_MICROSOFT_EXCHANGE_ONLINE,
    TEMPLATE_GOOGLE_MAIL,
]


class OAuth2MailError(Exception):
    """Domain error for OAuth2 mail token operations."""


class OAuth2NotAvailableError(OAuth2MailError):
    """Schema profile lacks OAuth tables/columns (Znuny &lt; 6.3)."""


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def ensure_oauth2_available() -> None:
    """Raise if the live schema does not support mail OAuth tables."""
    from tiqora.db.legacy.profile import get_legacy_schema_profile

    profile = get_legacy_schema_profile()
    if profile is None or not profile.mail_account_has_oauth:
        raise OAuth2NotAvailableError(
            "OAuth2 mail is not available on this legacy schema "
            "(requires Znuny 6.3+ mail_account OAuth columns and oauth2_* tables)"
        )


def parse_config_blob(raw: str | dict[str, Any] | None) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    text = str(raw).strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise OAuth2MailError(f"invalid OAuth2 config JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise OAuth2MailError("OAuth2 config JSON must be an object")
    return data


def dump_config_blob(config: dict[str, Any]) -> str:
    return json.dumps(config, ensure_ascii=False, separators=(",", ":"))


def authorization_callback_path() -> str:
    return "/api/v1/oauth2/callback"


def get_redirect_uri(settings: Settings | None = None) -> str:
    """Build the IdP redirect URI (Znuny: HttpType://FQDN/.../get-oauth2-…)."""
    cfg = settings or get_settings()
    base = (cfg.public_base_url or "").rstrip("/")
    if not base:
        # Dev-friendly fallback so authorize-url works without extra env.
        origins = [o.strip() for o in (cfg.cors_origins or "").split(",") if o.strip()]
        base = origins[0].rstrip("/") if origins else "http://localhost:8000"
    return f"{base}{authorization_callback_path()}"


def oauth2_state_redis_key(config_id: int, nonce: str) -> str:
    return f"{OAUTH2_STATE_REDIS_PREFIX}{int(config_id)}:{nonce}"


def state_for_config_id(config_id: int, nonce: str | None = None) -> str:
    base = f"{STATE_PREFIX}{int(config_id)}"
    return f"{base}.{nonce}" if nonce else base


def parse_token_config_id_from_state(state: str | None) -> int | None:
    if not state:
        return None
    m = STATE_RE.match(str(state))
    if not m:
        return None
    return int(m.group(1))


def parse_nonce_from_state(state: str | None) -> str | None:
    """Extract the anti-CSRF nonce from ``TokenConfigID<n>.<nonce>`` (or None)."""
    if not state:
        return None
    m = STATE_RE.match(str(state))
    if not m:
        return None
    return m.group(2)


def assemble_sasl_xoauth2_raw(username: str, access_token: str) -> bytes:
    """Raw SASL XOAUTH2 initial response (for ``imaplib.IMAP4.authenticate``).

    Python's ``authenticate`` base64-encodes the handler return value, so this
    must **not** be pre-encoded. Znuny's ``AssembleSASLAuthString`` returns
    base64 because ``Mail::IMAPClient`` does not re-encode.
    """
    payload = f"user={username}\x01auth=Bearer {access_token}\x01\x01"
    return payload.encode("utf-8")


def assemble_sasl_xoauth2_b64(username: str, access_token: str) -> str:
    """Base64 SASL string (POP3 AUTH / SMTP style, matches Znuny)."""
    return b64encode(assemble_sasl_xoauth2_raw(username, access_token)).decode("ascii")


def has_token_expired(token: OAuth2Token, *, now: datetime | None = None) -> bool:
    if not token.token:
        return True
    exp = token.token_expiration_date
    if exp is None:
        return False
    return exp <= (now or _utcnow_naive())


def has_refresh_token_expired(token: OAuth2Token, *, now: datetime | None = None) -> bool:
    if not token.refresh_token:
        return True
    exp = token.refresh_token_expiration_date
    if exp is None:
        # Znuny: no refresh expiry configured → treat as still valid.
        return False
    return exp <= (now or _utcnow_naive())


def token_status(token: OAuth2Token | None, *, now: datetime | None = None) -> str:
    """Coarse status for admin UI: none | valid | expired | error | needs_reauth."""
    if token is None:
        return "none"
    if (token.error_message or token.error_description or token.error_code) and (
        not token.token or has_token_expired(token, now=now)
    ):
        return "error"
    if not token.token:
        return "none"
    if has_token_expired(token, now=now):
        if has_refresh_token_expired(token, now=now):
            return "needs_reauth"
        return "expired"
    return "valid"


def _request_config(config: dict[str, Any], request_type: str) -> dict[str, Any]:
    requests = config.get("Requests") or {}
    if not isinstance(requests, dict):
        raise OAuth2MailError("config.Requests must be an object")
    block = requests.get(request_type)
    if not isinstance(block, dict):
        raise OAuth2MailError(f"config missing Requests.{request_type}")
    req = block.get("Request")
    if not isinstance(req, dict):
        raise OAuth2MailError(f"config missing Requests.{request_type}.Request")
    return req


def _response_mapping(config: dict[str, Any], request_type: str) -> dict[str, str]:
    requests = config.get("Requests") or {}
    block = requests.get(request_type) if isinstance(requests, dict) else None
    if not isinstance(block, dict):
        return {}
    resp = block.get("Response")
    if not isinstance(resp, dict):
        return {}
    mapping = resp.get("ParametersMapping")
    if not isinstance(mapping, dict):
        return {}
    return {str(k): str(v) for k, v in mapping.items() if v is not None}


def _assemble_request_data(
    *,
    config: dict[str, Any],
    token: OAuth2Token | None,
    config_id: int,
    request_type: str,
    settings: Settings | None,
    authorization_code: str | None = None,
    state_nonce: str | None = None,
) -> tuple[str, dict[str, str]]:
    req = _request_config(config, request_type)
    url = req.get("URL")
    if not url or not isinstance(url, str):
        raise OAuth2MailError(f"Requests.{request_type}.Request.URL missing")

    data: dict[str, str] = {}
    parameters = req.get("Parameters") or {}
    if isinstance(parameters, dict):
        for k, v in parameters.items():
            if v is not None:
                data[str(k)] = str(v)

    autofill = req.get("AutofilledParametersMapping") or {}
    if not isinstance(autofill, dict):
        return url, data

    token_fields: dict[str, str | None] = {}
    if token is not None:
        token_fields = {
            "AuthorizationCode": authorization_code
            if authorization_code is not None
            else token.authorization_code,
            "Token": token.token,
            "RefreshToken": token.refresh_token,
        }
    elif authorization_code is not None:
        token_fields = {"AuthorizationCode": authorization_code}

    for param, key in autofill.items():
        if key is None:
            continue
        key_s = str(key)
        value: str | None = None
        if key_s in config and config[key_s] is not None:
            value = str(config[key_s])
        elif key_s in token_fields and token_fields[key_s] is not None:
            value = str(token_fields[key_s])
        elif key_s == "State":
            value = state_for_config_id(config_id, state_nonce)
        elif key_s == "RedirectURL":
            value = get_redirect_uri(settings)
        data[str(param)] = value if value is not None else ""

    return url, data


def _ttl_to_expiration(ttl: object, *, now: datetime | None = None) -> datetime | None:
    """Convert OAuth ``expires_in`` seconds (or date string) to naive UTC datetime."""
    if ttl is None or ttl == "":
        return None
    base = now or _utcnow_naive()
    if isinstance(ttl, (int, float)):
        return base + timedelta(seconds=int(ttl))
    text = str(ttl).strip()
    if not text:
        return None
    try:
        seconds = int(float(text))
        return base + timedelta(seconds=seconds)
    except ValueError:
        pass
    # Already a datetime string from Znuny.
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.strptime(text[:26], fmt)
        except ValueError:
            continue
    return None


def _map_json_response(
    body: dict[str, Any],
    mapping: dict[str, str],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Map provider JSON fields to token record attributes (Znuny ParametersMapping)."""
    out: dict[str, Any] = {}
    for param, key in mapping.items():
        if param not in body:
            continue
        value = body[param]
        if isinstance(value, list):
            value = ", ".join(str(x) for x in value)
        elif isinstance(value, dict):
            value = json.dumps(value, ensure_ascii=False)
        if key in ("TokenExpirationDate", "RefreshTokenExpirationDate"):
            out[key] = _ttl_to_expiration(value, now=now)
        else:
            out[key] = None if value is None else str(value)
    return out


async def _post_form(
    url: str,
    data: dict[str, str],
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[int, dict[str, Any]]:
    try:
        pinned = pin_outbound_url(url)
    except OutboundURLError as exc:
        raise OAuth2MailError(f"outbound URL rejected: {exc}") from exc
    headers = pinned.request_headers(
        {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
    )
    extensions = pinned.request_extensions()
    async with httpx.AsyncClient(transport=transport, follow_redirects=False) as client:
        resp = await client.post(
            pinned.request_url,
            data=data,
            headers=headers,
            extensions=extensions,
        )
    try:
        body: dict[str, Any] = resp.json() if resp.content else {}
        if not isinstance(body, dict):
            body = {}
    except ValueError:
        body = {}
    return resp.status_code, body


async def get_config(session: AsyncSession, config_id: int) -> OAuth2TokenConfig | None:
    return await session.get(OAuth2TokenConfig, config_id)


async def get_config_by_name(session: AsyncSession, name: str) -> OAuth2TokenConfig | None:
    row = (
        await session.execute(
            select(OAuth2TokenConfig).where(OAuth2TokenConfig.name == name).limit(1)
        )
    ).scalar_one_or_none()
    return row


async def list_configs(
    session: AsyncSession, *, valid_only: bool = False
) -> list[OAuth2TokenConfig]:
    stmt = select(OAuth2TokenConfig).order_by(OAuth2TokenConfig.name)
    if valid_only:
        stmt = stmt.where(OAuth2TokenConfig.valid_id == VALID_ID_VALID)
    return list((await session.execute(stmt)).scalars().all())


async def get_token_row(session: AsyncSession, config_id: int) -> OAuth2Token | None:
    return (
        await session.execute(
            select(OAuth2Token).where(OAuth2Token.token_config_id == config_id).limit(1)
        )
    ).scalar_one_or_none()


async def get_or_create_token_row(
    session: AsyncSession, *, config_id: int, user_id: int
) -> OAuth2Token:
    row = await get_token_row(session, config_id)
    if row is not None:
        return row
    now = _utcnow_naive()
    row = OAuth2Token(
        token_config_id=config_id,
        authorization_code=None,
        token=None,
        token_expiration_date=None,
        refresh_token=None,
        refresh_token_expiration_date=None,
        error_message=None,
        error_description=None,
        error_code=None,
        create_time=now,
        create_by=user_id,
        change_time=now,
        change_by=user_id,
    )
    session.add(row)
    await session.flush()
    return row


async def create_config(
    session: AsyncSession,
    *,
    name: str,
    config: dict[str, Any],
    user_id: int,
    valid: bool = True,
) -> OAuth2TokenConfig:
    ensure_oauth2_available()
    now = _utcnow_naive()
    row = OAuth2TokenConfig(
        name=name.strip(),
        config=dump_config_blob(config),
        valid_id=VALID_ID_VALID if valid else VALID_ID_INVALID,
        create_time=now,
        create_by=user_id,
        change_time=now,
        change_by=user_id,
    )
    session.add(row)
    await session.flush()
    await get_or_create_token_row(session, config_id=row.id, user_id=user_id)
    await session.commit()
    await session.refresh(row)
    return row


async def update_config(
    session: AsyncSession,
    row: OAuth2TokenConfig,
    *,
    user_id: int,
    name: str | None = None,
    config: dict[str, Any] | None = None,
    valid: bool | None = None,
    client_secret: str | None = None,
) -> OAuth2TokenConfig:
    ensure_oauth2_available()
    if name is not None:
        row.name = name.strip()
    if config is not None:
        blob = dict(config)
        # Preserve existing secret when client posts empty/omitted secret.
        if client_secret is not None and client_secret != "":
            blob["ClientSecret"] = client_secret
        elif not blob.get("ClientSecret"):
            existing = parse_config_blob(row.config)
            if existing.get("ClientSecret"):
                blob["ClientSecret"] = existing["ClientSecret"]
        row.config = dump_config_blob(blob)
    elif client_secret is not None and client_secret != "":
        existing = parse_config_blob(row.config)
        existing["ClientSecret"] = client_secret
        row.config = dump_config_blob(existing)
    if valid is not None:
        row.valid_id = VALID_ID_VALID if valid else VALID_ID_INVALID
    row.change_time = _utcnow_naive()
    row.change_by = user_id
    await session.commit()
    await session.refresh(row)
    return row


async def delete_config(
    session: AsyncSession, row: OAuth2TokenConfig, *, hard: bool = True
) -> None:
    """Znuny hard-deletes config + token; soft-delete sets valid_id=2."""
    ensure_oauth2_available()
    if hard:
        token = await get_token_row(session, row.id)
        if token is not None:
            await session.delete(token)
        await session.delete(row)
    else:
        row.valid_id = VALID_ID_INVALID
        row.change_time = _utcnow_naive()
    await session.commit()


def build_authorization_url(
    row: OAuth2TokenConfig,
    *,
    settings: Settings | None = None,
    state_nonce: str | None = None,
) -> str:
    config = parse_config_blob(row.config)
    url, data = _assemble_request_data(
        config=config,
        token=None,
        config_id=row.id,
        request_type=REQUEST_AUTH_CODE,
        settings=settings,
        state_nonce=state_nonce,
    )
    if not data:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{urlencode(data)}"


async def request_token_by_authorization_code(
    session: AsyncSession,
    *,
    config_id: int,
    authorization_code: str,
    user_id: int = 1,
    settings: Settings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> OAuth2Token:
    ensure_oauth2_available()
    cfg_row = await get_config(session, config_id)
    if cfg_row is None:
        raise OAuth2MailError(f"token config {config_id} not found")
    config = parse_config_blob(cfg_row.config)
    token = await get_or_create_token_row(session, config_id=config_id, user_id=user_id)

    token.authorization_code = authorization_code
    token.error_message = None
    token.error_description = None
    token.error_code = None
    token.change_time = _utcnow_naive()
    token.change_by = user_id
    await session.flush()

    url, data = _assemble_request_data(
        config=config,
        token=token,
        config_id=config_id,
        request_type=REQUEST_TOKEN_BY_CODE,
        settings=settings,
        authorization_code=authorization_code,
    )

    # Clear authorization code after assembling request (Znuny behaviour).
    token.authorization_code = None
    await session.flush()

    status, body = await _post_form(url, data, transport=transport)
    mapped = _map_json_response(body, _response_mapping(config, REQUEST_TOKEN_BY_CODE))
    _apply_mapped_to_token(token, mapped, user_id=user_id)
    await session.commit()
    await session.refresh(token)

    if status != 200 or not token.token:
        err = get_token_error_message(token) or f"token endpoint HTTP {status}"
        logger.error(
            "oauth2_token_by_code_failed",
            config_id=config_id,
            status=status,
            error=err,
        )
        raise OAuth2MailError(err)
    return token


async def request_token_by_refresh_token(
    session: AsyncSession,
    *,
    config_id: int,
    user_id: int = 1,
    settings: Settings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> OAuth2Token:
    ensure_oauth2_available()
    cfg_row = await get_config(session, config_id)
    if cfg_row is None:
        raise OAuth2MailError(f"token config {config_id} not found")
    config = parse_config_blob(cfg_row.config)
    if REQUEST_TOKEN_BY_REFRESH not in (config.get("Requests") or {}):
        raise OAuth2MailError("refresh token request is not configured")

    token = await get_or_create_token_row(session, config_id=config_id, user_id=user_id)
    if has_refresh_token_expired(token):
        raise OAuth2MailError(
            "refresh token expired or missing; re-authorize via authorization code"
        )

    token.authorization_code = None
    token.error_message = None
    token.error_description = None
    token.error_code = None
    token.change_time = _utcnow_naive()
    token.change_by = user_id
    await session.flush()

    url, data = _assemble_request_data(
        config=config,
        token=token,
        config_id=config_id,
        request_type=REQUEST_TOKEN_BY_REFRESH,
        settings=settings,
    )
    status, body = await _post_form(url, data, transport=transport)
    mapped = _map_json_response(body, _response_mapping(config, REQUEST_TOKEN_BY_REFRESH))
    _apply_mapped_to_token(token, mapped, user_id=user_id)
    await session.commit()
    await session.refresh(token)

    if status != 200 or not token.token:
        err = get_token_error_message(token) or f"token endpoint HTTP {status}"
        logger.error(
            "oauth2_token_by_refresh_failed",
            config_id=config_id,
            status=status,
            error=err,
        )
        raise OAuth2MailError(err)
    return token


def _apply_mapped_to_token(token: OAuth2Token, mapped: dict[str, Any], *, user_id: int) -> None:
    # Only overwrite fields present in the response (Znuny issue #226).
    if "Token" in mapped:
        token.token = mapped["Token"]
    if "TokenExpirationDate" in mapped:
        token.token_expiration_date = mapped["TokenExpirationDate"]
    if "RefreshToken" in mapped and mapped["RefreshToken"]:
        token.refresh_token = mapped["RefreshToken"]
    if "RefreshTokenExpirationDate" in mapped:
        token.refresh_token_expiration_date = mapped["RefreshTokenExpirationDate"]
    if "ErrorMessage" in mapped:
        token.error_message = mapped["ErrorMessage"]
    if "ErrorDescription" in mapped:
        token.error_description = mapped["ErrorDescription"]
    if "ErrorCode" in mapped:
        token.error_code = mapped["ErrorCode"]
    token.change_time = _utcnow_naive()
    token.change_by = user_id


def get_token_error_message(token: OAuth2Token) -> str:
    parts: list[str] = []
    if token.error_message:
        parts.append(token.error_message)
    if token.error_code:
        if parts:
            parts[0] = f"{parts[0]} (error code {token.error_code})"
        else:
            parts.append(f"Error code {token.error_code}")
    if token.error_description:
        if parts:
            parts.append(f": {token.error_description}")
        else:
            parts.append(token.error_description)
    return "".join(parts)


async def get_access_token(
    session: AsyncSession,
    *,
    config_id: int,
    user_id: int = 1,
    settings: Settings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    """Return a usable access token, refreshing when expired (Znuny GetToken)."""
    ensure_oauth2_available()
    token = await get_token_row(session, config_id)
    if token is None:
        raise OAuth2MailError(f"no token row for config {config_id}")

    if has_token_expired(token):
        if has_refresh_token_expired(token):
            raise OAuth2MailError(
                "refresh token expired or missing; re-authorize via authorization code"
            )
        token = await request_token_by_refresh_token(
            session,
            config_id=config_id,
            user_id=user_id,
            settings=settings,
            transport=transport,
        )

    if not token.token:
        raise OAuth2MailError(f"no access token for config {config_id}")
    return token.token


async def handle_authorization_callback(
    session: AsyncSession,
    *,
    query_params: dict[str, str],
    user_id: int = 1,
    settings: Settings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> OAuth2Token:
    """Process IdP redirect query params (code + state=TokenConfigID{n})."""
    ensure_oauth2_available()
    # Prefer explicit state param; also scan all params for TokenConfigID* values
    # (Znuny walks all params via ParametersMapping → State).
    config_id = parse_token_config_id_from_state(query_params.get("state"))
    if config_id is None:
        for value in query_params.values():
            config_id = parse_token_config_id_from_state(value)
            if config_id is not None:
                break
    if config_id is None:
        raise OAuth2MailError("token config ID could not be retrieved from state")

    cfg_row = await get_config(session, config_id)
    if cfg_row is None:
        raise OAuth2MailError(f"token config {config_id} not found")
    config = parse_config_blob(cfg_row.config)
    mapping = _response_mapping(config, REQUEST_AUTH_CODE)
    # Default Znuny mapping: code → AuthorizationCode
    code_param = "code"
    for param, key in mapping.items():
        if key == "AuthorizationCode":
            code_param = param
            break
    code = query_params.get(code_param) or query_params.get("code")
    if not code:
        err = query_params.get("error_description") or query_params.get("error")
        raise OAuth2MailError(err or "authorization code missing from callback")

    return await request_token_by_authorization_code(
        session,
        config_id=config_id,
        authorization_code=code,
        user_id=user_id,
        settings=settings,
        transport=transport,
    )


def public_config_view(
    row: OAuth2TokenConfig,
    token: OAuth2Token | None,
    *,
    include_secret: bool = False,
) -> dict[str, Any]:
    """Serialize for admin API — secret redacted unless *include_secret*."""
    config = parse_config_blob(row.config)
    secret = str(config.get("ClientSecret") or "")
    if not include_secret:
        config = deepcopy(config)
        if "ClientSecret" in config:
            config["ClientSecret"] = ""
    return {
        "id": row.id,
        "name": row.name,
        "config": config,
        "client_id": str(config.get("ClientID") or ""),
        "has_client_secret": bool(secret),
        "scope": str(config.get("Scope") or ""),
        "valid": row.valid_id == VALID_ID_VALID,
        "token_status": token_status(token),
        "token_expiration_date": token.token_expiration_date if token else None,
        "refresh_token_expiration_date": (token.refresh_token_expiration_date if token else None),
        "has_token": bool(token and token.token),
        "has_refresh_token": bool(token and token.refresh_token),
        "error_message": get_token_error_message(token) if token else "",
        "create_time": row.create_time,
        "create_by": row.create_by,
        "change_time": row.change_time,
        "change_by": row.change_by,
        "redirect_uri": get_redirect_uri(),
    }


def list_provider_templates() -> list[dict[str, Any]]:
    return [
        {"id": "microsoft-exchange-online", **deepcopy(TEMPLATE_MICROSOFT_EXCHANGE_ONLINE)},
        {"id": "google-mail", **deepcopy(TEMPLATE_GOOGLE_MAIL)},
    ]


__all__ = [
    "OAuth2MailError",
    "OAuth2NotAvailableError",
    "PROVIDER_TEMPLATES",
    "assemble_sasl_xoauth2_b64",
    "assemble_sasl_xoauth2_raw",
    "authorization_callback_path",
    "build_authorization_url",
    "create_config",
    "delete_config",
    "dump_config_blob",
    "ensure_oauth2_available",
    "get_access_token",
    "get_config",
    "get_config_by_name",
    "get_or_create_token_row",
    "get_redirect_uri",
    "get_token_error_message",
    "get_token_row",
    "handle_authorization_callback",
    "has_refresh_token_expired",
    "has_token_expired",
    "list_configs",
    "list_provider_templates",
    "parse_config_blob",
    "parse_token_config_id_from_state",
    "public_config_view",
    "request_token_by_authorization_code",
    "request_token_by_refresh_token",
    "state_for_config_id",
    "token_status",
    "update_config",
]
