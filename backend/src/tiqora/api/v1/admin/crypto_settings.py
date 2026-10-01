"""Admin API for the PGP / S/MIME settings (Znuny SysConfig parity, editable in Tiqora).

``GET/PUT /api/v1/admin/crypto-settings`` — every setting with its effective
value and where it comes from; Tiqora values live in ``tiqora_settings``
(:mod:`tiqora.crypto.settings_store`). A value set by a ``TIQORA_CRYPTO_*``
env var or explicitly in Znuny's SysConfig wins and locks the field.

``PUT/DELETE /crypto-settings/pgp-passphrases/{key_ref}`` — the Tiqora part
of ``PGP::Key::Password``. A passphrase is checked with a test signature
before it is stored (Fernet-encrypted); passphrases are never returned.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from tiqora.api.deps import AppSettings, DbSession
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.crypto import CryptoError, CryptoNotFoundError
from tiqora.crypto import settings_store as store
from tiqora.crypto.config import resolve_crypto_config
from tiqora.crypto.keystore import record_audit
from tiqora.crypto.pgp import PgpEngine, PgpKeyInfo
from tiqora.znuny.sysconfig import SysConfig

router = APIRouter(prefix="/crypto-settings", tags=["admin:crypto"])


class CryptoSettingOut(BaseModel):
    name: str
    kind: str
    choices: list[str] = Field(default_factory=list)
    value: Any
    source: str
    locked: bool
    tiqora_value: Any = None
    znuny_setting: str | None = None
    env_var: str | None = None


class PgpPassphraseOut(BaseModel):
    fingerprint: str
    key_id: str
    uids: list[str]
    #: "znuny" | "tiqora" | "znuny_default" | "none"
    source: str


class CryptoSettingsOut(BaseModel):
    pgp: list[CryptoSettingOut]
    smime: list[CryptoSettingOut]
    pgp_passphrases: list[PgpPassphraseOut]
    #: Why the secret-key list is empty when the keyring is not usable.
    pgp_passphrases_error: str | None = None


class CryptoSettingsUpdate(BaseModel):
    """Field name → new Tiqora value; ``null`` clears the Tiqora value."""

    values: dict[str, Any]


class PgpPassphraseIn(BaseModel):
    passphrase: str = Field(min_length=1, max_length=1024)


async def _passphrase_rows(
    settings: AppSettings, sysconfig: SysConfig, tiqora: dict[str, str]
) -> tuple[list[PgpPassphraseOut], str | None]:
    cfg = await resolve_crypto_config(settings, sysconfig, tiqora)
    resolved = await store.resolve_passwords(settings, sysconfig, tiqora)
    try:
        engine = PgpEngine.from_config(cfg.pgp)
        keys = await asyncio.to_thread(engine.list_keys)
    except CryptoError as exc:
        return [], str(exc)
    rows: list[PgpPassphraseOut] = []
    for key in keys:
        if not key.has_secret:
            continue
        source = "none"
        for ident in [key.znuny_key_id, *sorted(key.all_ids())]:
            if ident in resolved.sources:
                source = resolved.sources[ident]
                break
        rows.append(
            PgpPassphraseOut(
                fingerprint=key.fingerprint, key_id=key.znuny_key_id, uids=key.uids, source=source
            )
        )
    return rows, None


async def _build_out(session: DbSession, settings: AppSettings) -> CryptoSettingsOut:
    sysconfig = SysConfig(session)
    tiqora = await store.load_tiqora_values(session)
    fields = await store.resolve_fields(settings, sysconfig, tiqora)
    out: dict[str, list[CryptoSettingOut]] = {"pgp": [], "smime": []}
    for f in fields.values():
        out[f.spec.backend].append(
            CryptoSettingOut(
                name=f.spec.name,
                kind=f.spec.kind,
                choices=list(f.spec.choices),
                value=f.value,
                source=f.source,
                locked=f.locked,
                tiqora_value=f.tiqora_value,
                znuny_setting=f.spec.znuny,
                env_var=f.spec.env_var,
            )
        )
    passphrases, error = await _passphrase_rows(settings, sysconfig, tiqora)
    return CryptoSettingsOut(
        pgp=out["pgp"],
        smime=out["smime"],
        pgp_passphrases=passphrases,
        pgp_passphrases_error=error,
    )


@router.get("", response_model=CryptoSettingsOut)
async def get_crypto_settings(
    admin: AdminUser, session: DbSession, settings: AppSettings
) -> CryptoSettingsOut:
    _ = admin
    return await _build_out(session, settings)


@router.put("", response_model=CryptoSettingsOut)
async def put_crypto_settings(
    body: CryptoSettingsUpdate, admin: AdminUser, session: DbSession, settings: AppSettings
) -> CryptoSettingsOut:
    """Store Tiqora values. 422 for unknown fields / invalid values, 409 for locked ones."""
    _ = admin
    fields = await store.resolve_fields(
        settings, SysConfig(session), await store.load_tiqora_values(session)
    )
    for name, value in body.values.items():
        spec = store.FIELDS_BY_NAME.get(name)
        if spec is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"unknown setting {name!r}"
            )
        if value is not None:
            if fields[name].locked:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    detail=f"{name} is set by {fields[name].source} and cannot be changed here",
                )
            try:
                store.validate_value(spec, value)
            except ValueError as exc:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    for name, value in body.values.items():
        await store.save_field(session, name, value)
    await session.commit()
    return await _build_out(session, settings)


async def _engine(session: DbSession, settings: AppSettings) -> PgpEngine:
    cfg = await resolve_crypto_config(settings, SysConfig(session))
    try:
        return PgpEngine.from_config(cfg.pgp)
    except CryptoError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


async def _find_secret(engine: PgpEngine, key_ref: str) -> PgpKeyInfo:
    try:
        key = await asyncio.to_thread(engine.find_key, key_ref)
    except CryptoError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    if key is None or not key.has_secret:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="PGP secret key not found")
    return key


@router.put("/pgp-passphrases/{key_ref}", response_model=CryptoSettingsOut)
async def put_pgp_passphrase(
    key_ref: str,
    body: PgpPassphraseIn,
    admin: AdminUser,
    session: DbSession,
    settings: AppSettings,
) -> CryptoSettingsOut:
    engine = await _engine(session, settings)
    key = await _find_secret(engine, key_ref)
    try:
        await asyncio.to_thread(engine.check_passphrase, key.fingerprint, body.passphrase)
    except CryptoNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CryptoError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    for ident in key.all_ids():  # drop entries stored under another id of the same key
        await store.set_passphrase(session, settings, ident, None)
    await store.set_passphrase(session, settings, key.znuny_key_id, body.passphrase)
    await record_audit(
        session,
        key_type="pgp",
        identifier=key.fingerprint,
        action="passphrase_set",
        user_id=admin.id,
        email=key.emails[0] if key.emails else None,
        has_private_key=True,
    )
    await session.commit()
    return await _build_out(session, settings)


@router.delete("/pgp-passphrases/{key_ref}", response_model=CryptoSettingsOut)
async def delete_pgp_passphrase(
    key_ref: str, admin: AdminUser, session: DbSession, settings: AppSettings
) -> CryptoSettingsOut:
    engine = await _engine(session, settings)
    key = await _find_secret(engine, key_ref)
    for ident in {key.znuny_key_id, *key.all_ids()}:
        await store.set_passphrase(session, settings, ident, None)
    await record_audit(
        session,
        key_type="pgp",
        identifier=key.fingerprint,
        action="passphrase_delete",
        user_id=admin.id,
        email=key.emails[0] if key.emails else None,
        has_private_key=True,
    )
    await session.commit()
    return await _build_out(session, settings)
