"""Customer PGP keys / S-MIME certificates for agents (Znuny customer preferences PGP/SMIME).

Znuny shows the customer preference modules ``PGP`` and ``SMIME`` in
``AdminCustomerUser`` (frontend module group ``admin`` or ``users``, ``rw``);
Tiqora offers them on the agent customer detail page and in the customer-user
admin. Reading is open to every agent (the material is public); upload and
delete need ``rw`` in ``admin`` or ``users``. Like Znuny's modules they only
exist while the backend (``PGP`` / ``SMIME``) is enabled.

The portal's self-service variant (``/api/portal/preferences``) reuses
:func:`load_customer_keys` and the upload helpers.
"""

from __future__ import annotations

import asyncio
from typing import NoReturn

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.api.deps import AppSettings, CurrentUser, DbSession
from tiqora.api.v1.admin.crypto_keys import (
    PgpKeyOut,
    SmimeCertOut,
    _certificate_bytes,
    _pgp_out,
    _smime_out,
)
from tiqora.crypto import CryptoError, CryptoNotFoundError, CryptoUnavailableError
from tiqora.crypto import customer_keys as ck
from tiqora.crypto.config import CryptoConfig, load_crypto_config
from tiqora.crypto.pgp import PgpEngine
from tiqora.crypto.smime_store import SmimeStore
from tiqora.db.legacy.customer import CustomerUser
from tiqora.permissions.engine import PermissionEngine

router = APIRouter(prefix="/customers", tags=["customers"])

#: Znuny ``Frontend::Module###AdminCustomerUser`` → ``Group``.
CUSTOMER_ADMIN_GROUPS: tuple[str, ...] = ("admin", "users")


class CustomerCryptoKeysOut(BaseModel):
    """A customer's keys in the shared stores + the Znuny preferences pointing at them."""

    pgp_enabled: bool
    smime_enabled: bool
    #: The caller may upload/delete (agents: rw in admin or users).
    can_edit: bool = False
    pgp_keys: list[PgpKeyOut] = Field(default_factory=list)
    smime_certificates: list[SmimeCertOut] = Field(default_factory=list)
    #: customer_preferences ``PGPKeyID`` / ``SMIMEFilename`` (last upload).
    pgp_key_id: str | None = None
    smime_filename: str | None = None
    #: Backend enabled but not usable (binary/store missing) — shown, not raised.
    problems: list[str] = Field(default_factory=list)


class CustomerPgpKeyIn(BaseModel):
    ascii_armor: str = Field(min_length=20)


class CustomerSmimeCertificateIn(BaseModel):
    #: PEM text, or base64 of a DER / PKCS#7 / PKCS#12 (no passphrase) file.
    certificate: str = Field(min_length=20)


def raise_crypto(exc: Exception) -> NoReturn:
    if isinstance(exc, CryptoUnavailableError):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    if isinstance(exc, CryptoNotFoundError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


def pgp_engine(config: CryptoConfig) -> PgpEngine:
    if not config.pgp.enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="PGP is not enabled")
    try:
        return PgpEngine.from_config(config.pgp)
    except CryptoError as exc:
        raise_crypto(exc)


def smime_store(config: CryptoConfig) -> SmimeStore:
    if not config.smime.enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="S/MIME is not enabled")
    try:
        return SmimeStore.from_config(config.smime)
    except CryptoError as exc:
        raise_crypto(exc)


async def load_customer_keys(
    session: AsyncSession, config: CryptoConfig, login: str, email: str, *, can_edit: bool
) -> CustomerCryptoKeysOut:
    prefs = await ck.get_preferences(session, login)
    out = CustomerCryptoKeysOut(
        pgp_enabled=config.pgp.enabled,
        smime_enabled=config.smime.enabled,
        can_edit=can_edit,
        pgp_key_id=prefs.get("PGPKeyID") or None,
        smime_filename=prefs.get("SMIMEFilename") or None,
    )
    if config.pgp.enabled:
        try:
            engine = PgpEngine.from_config(config.pgp)
            keys = await asyncio.to_thread(ck.customer_pgp_keys, engine, email, prefs)
            out.pgp_keys = [_pgp_out(k) for k in keys]
        except CryptoError as exc:
            out.problems.append(f"PGP: {exc}")
    if config.smime.enabled:
        try:
            store = SmimeStore.from_config(config.smime)
            certs = await asyncio.to_thread(ck.customer_smime_certs, store, email, prefs)
            out.smime_certificates = [_smime_out(e) for e in certs]
        except CryptoError as exc:
            out.problems.append(f"S/MIME: {exc}")
    return out


async def upload_pgp(
    session: AsyncSession,
    config: CryptoConfig,
    *,
    login: str,
    email: str,
    armored: str,
    user_id: int | None,
    require_own_email: bool,
) -> None:
    engine = pgp_engine(config)
    try:
        await ck.add_customer_pgp_key(
            session,
            engine,
            login=login,
            email=email,
            armored=armored,
            user_id=user_id,
            require_own_email=require_own_email,
        )
    except CryptoError as exc:
        await session.rollback()
        raise_crypto(exc)


async def upload_smime(
    session: AsyncSession,
    config: CryptoConfig,
    *,
    login: str,
    email: str,
    certificate: str,
    user_id: int | None,
    require_own_email: bool,
) -> None:
    store = smime_store(config)
    try:
        await ck.add_customer_smime_certificate(
            session,
            store,
            login=login,
            email=email,
            data=_certificate_bytes(certificate),
            user_id=user_id,
            require_own_email=require_own_email,
        )
    except CryptoError as exc:
        await session.rollback()
        raise_crypto(exc)


# --------------------------------------------------------------- agent routes


async def _customer(session: AsyncSession, login: str) -> CustomerUser:
    cu = (
        await session.execute(select(CustomerUser).where(CustomerUser.login == login))
    ).scalar_one_or_none()
    if cu is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")
    return cu


async def _can_edit(session: AsyncSession, user_id: int) -> bool:
    return await PermissionEngine(session).has_rw_in_any_group(user_id, CUSTOMER_ADMIN_GROUPS)


async def _require_edit(session: AsyncSession, user_id: int) -> None:
    if not await _can_edit(session, user_id):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="Managing customer keys needs rw in the admin or users group",
        )


@router.get("/{login}/crypto-keys", response_model=CustomerCryptoKeysOut)
async def get_customer_crypto_keys(
    login: str, user: CurrentUser, session: DbSession, settings: AppSettings
) -> CustomerCryptoKeysOut:
    cu = await _customer(session, login)
    config = await load_crypto_config(session, settings)
    return await load_customer_keys(
        session, config, cu.login, cu.email, can_edit=await _can_edit(session, user.id)
    )


@router.post(
    "/{login}/crypto-keys/pgp",
    response_model=CustomerCryptoKeysOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_customer_pgp_key(
    login: str,
    body: CustomerPgpKeyIn,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> CustomerCryptoKeysOut:
    """Import the customer's public key (Znuny customer preference ``PGP``)."""
    await _require_edit(session, user.id)
    cu = await _customer(session, login)
    config = await load_crypto_config(session, settings)
    await upload_pgp(
        session,
        config,
        login=cu.login,
        email=cu.email,
        armored=body.ascii_armor,
        user_id=user.id,
        require_own_email=False,
    )
    return await load_customer_keys(session, config, cu.login, cu.email, can_edit=True)


@router.post(
    "/{login}/crypto-keys/smime",
    response_model=CustomerCryptoKeysOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_customer_smime_certificate(
    login: str,
    body: CustomerSmimeCertificateIn,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> CustomerCryptoKeysOut:
    """Store the customer's certificate (Znuny customer preference ``SMIME``)."""
    await _require_edit(session, user.id)
    cu = await _customer(session, login)
    config = await load_crypto_config(session, settings)
    await upload_smime(
        session,
        config,
        login=cu.login,
        email=cu.email,
        certificate=body.certificate,
        user_id=user.id,
        require_own_email=False,
    )
    return await load_customer_keys(session, config, cu.login, cu.email, can_edit=True)


@router.delete("/{login}/crypto-keys/pgp/{key_id}", response_model=CustomerCryptoKeysOut)
async def delete_customer_pgp_key(
    login: str, key_id: str, user: CurrentUser, session: DbSession, settings: AppSettings
) -> CustomerCryptoKeysOut:
    await _require_edit(session, user.id)
    cu = await _customer(session, login)
    config = await load_crypto_config(session, settings)
    engine = pgp_engine(config)
    try:
        await ck.delete_customer_pgp_key(
            session, engine, login=cu.login, email=cu.email, key_ref=key_id, user_id=user.id
        )
    except CryptoError as exc:
        await session.rollback()
        raise_crypto(exc)
    return await load_customer_keys(session, config, cu.login, cu.email, can_edit=True)


@router.delete("/{login}/crypto-keys/smime/{filename}", response_model=CustomerCryptoKeysOut)
async def delete_customer_smime_certificate(
    login: str, filename: str, user: CurrentUser, session: DbSession, settings: AppSettings
) -> CustomerCryptoKeysOut:
    await _require_edit(session, user.id)
    cu = await _customer(session, login)
    config = await load_crypto_config(session, settings)
    store = smime_store(config)
    try:
        await ck.delete_customer_smime_certificate(
            session, store, login=cu.login, email=cu.email, filename=filename, user_id=user.id
        )
    except CryptoError as exc:
        await session.rollback()
        raise_crypto(exc)
    return await load_customer_keys(session, config, cu.login, cu.email, can_edit=True)
