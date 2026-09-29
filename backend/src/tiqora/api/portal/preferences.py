"""Customer portal preferences (Znuny ``CustomerPreferences``).

The groups Znuny's customer interface shows by default:

* ``CustomerPreferencesGroups###Language`` — ``UserLanguage`` preference;
* ``CustomerPreferencesGroups###Password`` — change the ``customer_user``
  password (current password required; Tiqora's password length policy);
* ``CustomerPreferencesGroups###PGP`` / ``###SMIME`` — upload the customer's
  own PGP key / S/MIME certificate, only while the backend (``PGP`` /
  ``SMIME``) is enabled, like Znuny's ``Param()`` returning nothing otherwise.

A group deactivated in Znuny's SysConfig (setting invalid, or ``Active`` 0)
is hidden and its endpoint answers 404. Key uploads go into the shared
stores and set the same customer preferences as Znuny
(:mod:`tiqora.crypto.customer_keys`); the key must carry the customer's own
email address. Deleting keys is left to agents.
"""

from __future__ import annotations

import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.api.deps import get_redis
from tiqora.api.portal.deps import AppSettings, CurrentCustomer, DbSession
from tiqora.api.v1.customer_keys import (
    CustomerCryptoKeysOut,
    CustomerPgpKeyIn,
    CustomerSmimeCertificateIn,
    load_customer_keys,
    upload_pgp,
    upload_smime,
)
from tiqora.crypto import customer_keys as ck
from tiqora.crypto.config import load_crypto_config, setting_is_valid
from tiqora.db.legacy.customer import CustomerUser
from tiqora.domain.customer_auth import AuthenticatedCustomer
from tiqora.domain.password_policy import PasswordPolicyError, validate_password
from tiqora.security.ratelimit import AuthRateLimiter, client_ip
from tiqora.znuny.password import hash_password, verify_password
from tiqora.znuny.sysconfig import SysConfig

router = APIRouter(prefix="/preferences", tags=["portal-preferences"])

_LANGUAGE_RE = re.compile(r"^[a-z]{2,3}(?:_[A-Za-z]{2,4})?$")

GROUP_LANGUAGE = "CustomerPreferencesGroups###Language"
GROUP_PASSWORD = "CustomerPreferencesGroups###Password"
GROUP_PGP = "CustomerPreferencesGroups###PGP"
GROUP_SMIME = "CustomerPreferencesGroups###SMIME"


class PortalPreferencesOut(BaseModel):
    #: ``UserLanguage`` (None: not chosen yet → browser/default language).
    language: str | None = None
    language_enabled: bool
    password_enabled: bool
    pgp_enabled: bool
    smime_enabled: bool
    keys: CustomerCryptoKeysOut


class PortalLanguageIn(BaseModel):
    language: str = Field(min_length=2, max_length=10)


class PortalPasswordIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=1, max_length=1024)


async def group_active(session: AsyncSession, name: str) -> bool:
    """Znuny preference group registered, valid and ``Active``."""
    if not await setting_is_valid(session, name):
        return False
    try:
        value: Any = await SysConfig(session).get(name)
    except Exception:  # noqa: BLE001 — SysConfig tables absent (fresh test DB)
        value = None
    return not (isinstance(value, dict) and str(value.get("Active", "1")).strip() == "0")


async def _build(
    session: AsyncSession, settings: AppSettings, customer: AuthenticatedCustomer
) -> PortalPreferencesOut:
    config = await load_crypto_config(session, settings)
    pgp = config.pgp.enabled and await group_active(session, GROUP_PGP)
    smime = config.smime.enabled and await group_active(session, GROUP_SMIME)
    keys = await load_customer_keys(
        session,
        config,
        customer.login,
        customer.email,
        can_edit=pgp or smime,
    )
    keys.pgp_enabled = pgp
    keys.smime_enabled = smime
    if not pgp:
        keys.pgp_keys = []
    if not smime:
        keys.smime_certificates = []
    keys.problems = []  # server-side detail, not for customers
    prefs = await ck.get_preferences(session, customer.login)
    return PortalPreferencesOut(
        language=prefs.get("UserLanguage") or None,
        language_enabled=await group_active(session, GROUP_LANGUAGE),
        password_enabled=await group_active(session, GROUP_PASSWORD),
        pgp_enabled=pgp,
        smime_enabled=smime,
        keys=keys,
    )


async def _require(session: AsyncSession, name: str) -> None:
    if not await group_active(session, name):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not Found")


async def get_password_limiter(request: Request, settings: AppSettings) -> AuthRateLimiter:
    return AuthRateLimiter(await get_redis(request), settings)


PasswordLimiter = Annotated[AuthRateLimiter, Depends(get_password_limiter)]


@router.get("", response_model=PortalPreferencesOut)
async def get_preferences(
    customer: CurrentCustomer, session: DbSession, settings: AppSettings
) -> PortalPreferencesOut:
    return await _build(session, settings, customer)


@router.put("/language", response_model=PortalPreferencesOut)
async def set_language(
    body: PortalLanguageIn, customer: CurrentCustomer, session: DbSession, settings: AppSettings
) -> PortalPreferencesOut:
    await _require(session, GROUP_LANGUAGE)
    if not _LANGUAGE_RE.match(body.language):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid language code")
    await ck.set_preferences(session, customer.login, {"UserLanguage": body.language})
    await session.commit()
    return await _build(session, settings, customer)


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: PortalPasswordIn,
    request: Request,
    customer: CurrentCustomer,
    session: DbSession,
    limiter: PasswordLimiter,
) -> None:
    """Znuny ``Preferences::Password`` (customer): verify the current one, store the new one."""
    await _require(session, GROUP_PASSWORD)
    ip = client_ip(request)
    key = f"portal-password:{customer.login}"
    pre = await limiter.check(login=key, ip=ip)
    if not pre.allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many attempts; try again later"
        )
    user = (
        await session.execute(
            select(CustomerUser).where(CustomerUser.id == customer.id, CustomerUser.valid_id == 1)
        )
    ).scalar_one_or_none()
    if user is None or not user.pw or not verify_password(body.current_password, user.pw):
        await limiter.record_failure(login=key, ip=ip)
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, detail="The current password is not correct"
        )
    if body.new_password == body.current_password:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The new password must differ from the current one",
        )
    try:
        validate_password(body.new_password)
    except PasswordPolicyError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    user.pw = hash_password(body.new_password)
    await session.commit()
    await limiter.reset(login=key, ip=ip)


@router.post("/pgp-key", response_model=PortalPreferencesOut, status_code=status.HTTP_201_CREATED)
async def upload_pgp_key(
    body: CustomerPgpKeyIn, customer: CurrentCustomer, session: DbSession, settings: AppSettings
) -> PortalPreferencesOut:
    await _require(session, GROUP_PGP)
    config = await load_crypto_config(session, settings)
    if not config.pgp.enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not Found")
    await upload_pgp(
        session,
        config,
        login=customer.login,
        email=customer.email,
        armored=body.ascii_armor,
        user_id=None,
        require_own_email=True,
    )
    return await _build(session, settings, customer)


@router.post(
    "/smime-certificate",
    response_model=PortalPreferencesOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_smime_certificate(
    body: CustomerSmimeCertificateIn,
    customer: CurrentCustomer,
    session: DbSession,
    settings: AppSettings,
) -> PortalPreferencesOut:
    await _require(session, GROUP_SMIME)
    config = await load_crypto_config(session, settings)
    if not config.smime.enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not Found")
    await upload_smime(
        session,
        config,
        login=customer.login,
        email=customer.email,
        certificate=body.certificate,
        user_id=None,
        require_own_email=True,
    )
    return await _build(session, settings, customer)
