"""Admin API for PGP / S-MIME key management (Znuny AdminPGP / AdminSMIME parity).

Keys live in the stores shared with Znuny (gpg keyring from ``PGP::Options
--homedir``, ``SMIME::CertPath``/``SMIME::PrivatePath`` in Znuny's layout);
see :mod:`tiqora.crypto.config`. Every mutation writes a ``tiqora_crypto_key``
audit row. Secrets (private key material, passphrases) are never returned.

Key management works whenever the store is configured, even while the
backend switch (``PGP``/``SMIME``) is off — keys can be prepared before
signing/encryption is enabled; ``GET /status`` reports both.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
from collections.abc import Callable
from datetime import datetime
from typing import NoReturn

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from tiqora.api.deps import AppSettings, DbSession
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.api.v1.admin.schemas import BinaryStatusOut, CryptoBackendStatusOut
from tiqora.crypto import CryptoError, CryptoNotFoundError, CryptoUnavailableError
from tiqora.crypto import keystore as ks
from tiqora.crypto.config import (
    BackendStatus,
    CryptoConfig,
    backend_status_sync,
    load_crypto_config,
)
from tiqora.crypto.pgp import PgpEngine, PgpKeyInfo
from tiqora.crypto.smime_store import SmimeEntry, SmimeStore
from tiqora.db.legacy.queue import Queue, SystemAddress
from tiqora.db.tiqora.models import TiqoraCryptoKey

router = APIRouter(prefix="/crypto-keys", tags=["admin:crypto"])

# --------------------------------------------------------------------- schemas


class CryptoKeyOut(BaseModel):
    """One audit row (not key material)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    key_type: str
    identifier: str
    email: str | None = None
    purpose: str
    has_private_key: bool
    action: str = "import"
    user_id: int | None = None
    detail: str | None = None
    created: datetime | None = None


class PgpKeyOut(BaseModel):
    fingerprint: str
    key_id: str
    short_id: str
    znuny_key_id: str
    uids: list[str]
    emails: list[str]
    created: datetime | None = None
    expires: datetime | None = None
    status: str
    has_secret: bool
    bits: int | None = None
    algorithm: str
    subkey_ids: list[str]


class PgpUploadIn(BaseModel):
    ascii_armor: str = Field(min_length=20)


class PgpUploadOut(BaseModel):
    fingerprints: list[str]
    keys: list[PgpKeyOut]


class SmimeCertOut(BaseModel):
    filename: str
    valid: bool  # False: the file is not a readable certificate
    hash: str
    subject: str
    issuer: str
    fingerprint: str
    serial: str
    not_before: datetime | None = None
    not_after: datetime | None = None
    emails: list[str]
    status: str  # valid | expired | invalid
    has_private: bool
    is_ca: bool


class SmimeCertificateIn(BaseModel):
    #: PEM text, or base64 of a DER certificate.
    certificate: str = Field(min_length=20)


class SmimePrivateKeyIn(BaseModel):
    private_key: str = Field(min_length=20)
    #: Passphrase of the key. Empty for an unencrypted key: it is then stored
    #: encrypted with a generated secret (Znuny requires one).
    secret: str = ""


class SmimePrivateKeyOut(BaseModel):
    certificate: SmimeCertOut
    secret_generated: bool


class SmimeDeleteOut(BaseModel):
    renamed: dict[str, str]


class SmimeRelationIn(BaseModel):
    ca_filename: str


class SmimeRelationOut(BaseModel):
    ca_filename: str | None = None
    ca_fingerprint: str
    ca_subject: str | None = None
    ca_not_after: datetime | None = None
    created: datetime | None = None


class SignKeyOptionOut(BaseModel):
    value: str
    backend: str
    method: str
    key: str
    label: str
    status: str
    expires: datetime | None = None
    emails: list[str]


class PgpImportIn(BaseModel):
    ascii_armor: str = Field(min_length=20)
    email: str | None = None
    purpose: str = "both"


class PgpImportOut(BaseModel):
    fingerprints: list[str]


class SmimeRegisterIn(BaseModel):
    email: str
    cert_pem: str | None = None
    key_pem: str | None = None
    secret: str = ""
    purpose: str = "both"


class SmimeRegisterOut(BaseModel):
    email: str
    has_cert: bool
    has_key: bool
    filename: str | None = None


# --------------------------------------------------------------------- helpers


def _raise(exc: Exception) -> NoReturn:
    if isinstance(exc, CryptoUnavailableError):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    if isinstance(exc, CryptoNotFoundError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


async def _config(session: DbSession, settings: AppSettings) -> CryptoConfig:
    return await load_crypto_config(session, settings)


async def _pgp(session: DbSession, settings: AppSettings) -> PgpEngine:
    cfg = await _config(session, settings)
    try:
        return PgpEngine.from_config(cfg.pgp)
    except CryptoError as exc:
        _raise(exc)


async def _smime(session: DbSession, settings: AppSettings) -> SmimeStore:
    cfg = await _config(session, settings)
    try:
        return SmimeStore.from_config(cfg.smime)
    except CryptoError as exc:
        _raise(exc)


async def _thread[T](fn: Callable[..., T], *args: object) -> T:
    try:
        return await asyncio.to_thread(fn, *args)
    except CryptoError as exc:
        _raise(exc)


def _pgp_out(key: PgpKeyInfo) -> PgpKeyOut:
    return PgpKeyOut(
        fingerprint=key.fingerprint,
        key_id=key.key_id,
        short_id=key.short_id,
        znuny_key_id=key.znuny_key_id,
        uids=key.uids,
        emails=key.emails,
        created=key.created,
        expires=key.expires,
        status=key.status,
        has_secret=key.has_secret,
        bits=key.bits,
        algorithm=key.algorithm,
        subkey_ids=key.subkey_ids,
    )


def _smime_out(entry: SmimeEntry) -> SmimeCertOut:
    info = entry.info
    if info is None:
        return SmimeCertOut(
            filename=entry.filename,
            valid=False,
            hash=entry.filename.split(".", 1)[0],
            subject=f"The file '{entry.filename}' is invalid",
            issuer="",
            fingerprint="",
            serial="",
            emails=[],
            status="invalid",
            has_private=entry.has_private,
            is_ca=False,
        )
    return SmimeCertOut(
        filename=entry.filename,
        valid=True,
        hash=info.hash,
        subject=info.subject,
        issuer=info.issuer,
        fingerprint=info.fingerprint,
        serial=info.serial,
        not_before=info.not_before,
        not_after=info.not_after,
        emails=info.emails,
        status=ks.smime_status(entry),
        has_private=entry.has_private,
        is_ca=info.is_ca,
    )


def status_out(st: BackendStatus) -> CryptoBackendStatusOut:
    return CryptoBackendStatusOut(
        backend=st.backend,
        enabled=st.enabled,
        available=st.available,
        binary=BinaryStatusOut(
            available=st.binary.available,
            path=st.binary.path,
            version=st.binary.version,
            reason=st.binary.reason,
        ),
        paths=st.paths,
        problems=st.problems,
    )


def _certificate_bytes(text: str) -> bytes:
    """PEM text as-is; anything else is taken as base64 DER (file upload of a .cer/.der)."""
    if "-----BEGIN" in text:
        return text.encode("utf-8")
    try:
        return base64.b64decode("".join(text.split()), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="certificate must be PEM or base64-encoded DER",
        ) from exc


# ----------------------------------------------------------------- audit/status


@router.get("", response_model=list[CryptoKeyOut])
async def list_crypto_keys(admin: AdminUser, session: DbSession) -> list[TiqoraCryptoKey]:
    """Audit trail of key-store mutations (newest first; never key material)."""
    _ = admin
    rows = (
        await session.execute(
            select(TiqoraCryptoKey).order_by(TiqoraCryptoKey.id.desc()).limit(500)
        )
    ).scalars()
    return list(rows)


@router.get("/status", response_model=list[CryptoBackendStatusOut])
async def crypto_status(
    admin: AdminUser, session: DbSession, settings: AppSettings
) -> list[CryptoBackendStatusOut]:
    """Per backend: SysConfig switch, binary self-check, key directories."""
    _ = admin
    cfg = await _config(session, settings)
    return [status_out(s) for s in await asyncio.to_thread(backend_status_sync, cfg)]


# ------------------------------------------------------------------------- PGP


@router.get("/pgp", response_model=list[PgpKeyOut])
async def list_pgp_keys(
    admin: AdminUser, session: DbSession, settings: AppSettings
) -> list[PgpKeyOut]:
    _ = admin
    engine = await _pgp(session, settings)
    keys = await _thread(engine.list_keys)
    return [_pgp_out(k) for k in keys]


@router.post("/pgp", response_model=PgpUploadOut, status_code=status.HTTP_201_CREATED)
async def upload_pgp_key(
    body: PgpUploadIn, admin: AdminUser, session: DbSession, settings: AppSettings
) -> PgpUploadOut:
    """Import an ASCII-armored public or secret key (Znuny AdminPGP ``AddKey``)."""
    engine = await _pgp(session, settings)
    try:
        fps = await ks.import_pgp_key(session, engine, body.ascii_armor, user_id=admin.id)
    except CryptoError as exc:
        _raise(exc)
    keys = await _thread(engine.list_keys)
    wanted = {fp.upper() for fp in fps}
    return PgpUploadOut(
        fingerprints=fps, keys=[_pgp_out(k) for k in keys if k.fingerprint in wanted]
    )


@router.delete("/pgp/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_pgp_key(
    key_id: str,
    admin: AdminUser,
    session: DbSession,
    settings: AppSettings,
    secret: bool = Query(False, description="Delete only the secret key, keep the public key"),
) -> Response:
    engine = await _pgp(session, settings)
    try:
        await ks.delete_pgp_key(session, engine, key_id, secret_only=secret, user_id=admin.id)
    except CryptoError as exc:
        _raise(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/pgp/{key_id}/export",
    response_class=Response,
    responses={200: {"content": {"application/pgp-keys": {}}}},
)
async def export_pgp_key(
    key_id: str, admin: AdminUser, session: DbSession, settings: AppSettings
) -> Response:
    """Armored **public** key (secret keys are never exported)."""
    _ = admin
    engine = await _pgp(session, settings)
    armored = await _thread(engine.export_public, key_id)
    safe = "".join(c for c in key_id if c.isalnum())[:40] or "key"
    return Response(
        content=armored,
        media_type="application/pgp-keys",
        headers={"Content-Disposition": f'attachment; filename="{safe}.asc"'},
    )


# ---------------------------------------------------------------------- S/MIME


@router.get("/smime", response_model=list[SmimeCertOut])
async def list_smime(
    admin: AdminUser, session: DbSession, settings: AppSettings
) -> list[SmimeCertOut]:
    _ = admin
    store = await _smime(session, settings)
    entries = await _thread(store.list_entries)
    return [_smime_out(e) for e in entries]


@router.post(
    "/smime/certificates", response_model=SmimeCertOut, status_code=status.HTTP_201_CREATED
)
async def upload_smime_certificate(
    body: SmimeCertificateIn, admin: AdminUser, session: DbSession, settings: AppSettings
) -> SmimeCertOut:
    store = await _smime(session, settings)
    try:
        entry = await ks.add_smime_certificate(
            session, store, _certificate_bytes(body.certificate), user_id=admin.id
        )
    except CryptoError as exc:
        _raise(exc)
    return _smime_out(entry)


@router.post(
    "/smime/private-keys",
    response_model=SmimePrivateKeyOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_smime_private_key(
    body: SmimePrivateKeyIn, admin: AdminUser, session: DbSession, settings: AppSettings
) -> SmimePrivateKeyOut:
    """Private key + secret for an already stored certificate (Znuny ``AddPrivate``)."""
    store = await _smime(session, settings)
    try:
        entry, generated = await ks.add_smime_private_key(
            session, store, body.private_key.encode("utf-8"), body.secret, user_id=admin.id
        )
    except CryptoError as exc:
        _raise(exc)
    return SmimePrivateKeyOut(certificate=_smime_out(entry), secret_generated=generated)


@router.delete("/smime/{filename}", response_model=SmimeDeleteOut)
async def delete_smime(
    filename: str,
    admin: AdminUser,
    session: DbSession,
    settings: AppSettings,
    private_only: bool = Query(False, description="Delete only the private key + secret"),
) -> SmimeDeleteOut:
    """Delete a certificate (with its private key and relations) or only the private key."""
    store = await _smime(session, settings)
    try:
        renamed = await ks.delete_smime(
            session, store, filename, private_only=private_only, user_id=admin.id
        )
    except CryptoError as exc:
        _raise(exc)
    return SmimeDeleteOut(renamed=renamed)


@router.get(
    "/smime/{filename}",
    response_class=Response,
    responses={200: {"content": {"application/x-pem-file": {}}}},
)
async def download_smime_certificate(
    filename: str, admin: AdminUser, session: DbSession, settings: AppSettings
) -> Response:
    """The certificate PEM (never the private key)."""
    _ = admin
    store = await _smime(session, settings)
    pem = await _thread(store.get_certificate, filename)
    return Response(
        content=pem,
        media_type="application/x-pem-file",
        headers={"Content-Disposition": f'attachment; filename="{filename}.pem"'},
    )


@router.get("/smime/{filename}/relations", response_model=list[SmimeRelationOut])
async def list_smime_relations(
    filename: str, admin: AdminUser, session: DbSession, settings: AppSettings
) -> list[SmimeRelationOut]:
    """CA certificates attached to signatures made with this certificate."""
    _ = admin
    store = await _smime(session, settings)
    try:
        rows = await ks.list_smime_relations(session, store, filename)
    except CryptoError as exc:
        _raise(exc)
    return [SmimeRelationOut(**r.__dict__) for r in rows]


@router.post(
    "/smime/{filename}/relations",
    response_model=list[SmimeRelationOut],
    status_code=status.HTTP_201_CREATED,
)
async def add_smime_relation(
    filename: str,
    body: SmimeRelationIn,
    admin: AdminUser,
    session: DbSession,
    settings: AppSettings,
) -> list[SmimeRelationOut]:
    store = await _smime(session, settings)
    try:
        await ks.add_smime_relation(session, store, filename, body.ca_filename, user_id=admin.id)
        rows = await ks.list_smime_relations(session, store, filename)
    except CryptoError as exc:
        _raise(exc)
    return [SmimeRelationOut(**r.__dict__) for r in rows]


@router.delete("/smime/{filename}/relations", status_code=status.HTTP_204_NO_CONTENT)
async def delete_smime_relation(
    filename: str,
    admin: AdminUser,
    session: DbSession,
    settings: AppSettings,
    ca_fingerprint: str = Query(..., min_length=10),
) -> Response:
    store = await _smime(session, settings)
    try:
        await ks.delete_smime_relation(session, store, filename, ca_fingerprint, user_id=admin.id)
    except CryptoError as exc:
        _raise(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------ sign-key picker


@router.get("/sign-key-options", response_model=list[SignKeyOptionOut])
async def sign_key_options(
    admin: AdminUser,
    session: DbSession,
    settings: AppSettings,
    queue_id: int | None = Query(
        None, description="Only keys for this queue's system address (Znuny AdminQueue)"
    ),
    email: str | None = Query(None, description="Only keys for this address"),
) -> list[SignKeyOptionOut]:
    """Values for ``queue.default_sign_key`` in Znuny format (``PGP::Detached::<id>`` …)."""
    _ = admin
    if queue_id is not None and not email:
        row = (
            await session.execute(
                select(SystemAddress.value0)
                .join(Queue, Queue.system_address_id == SystemAddress.id)
                .where(Queue.id == queue_id)
            )
        ).first()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Queue not found")
        email = str(row[0])
    cfg = await _config(session, settings)
    options = await ks.sign_key_options(cfg, email=email)
    return [SignKeyOptionOut(**o.__dict__) for o in options]


# ------------------------------------------------------------ legacy aliases


@router.post("/pgp-import", response_model=PgpImportOut, status_code=status.HTTP_201_CREATED)
async def admin_pgp_import(
    body: PgpImportIn,
    admin: AdminUser,
    session: DbSession,
    settings: AppSettings,
) -> PgpImportOut:
    """Alias of ``POST /pgp`` (kept for existing API clients)."""
    engine = await _pgp(session, settings)
    try:
        fps = await ks.import_pgp_key(
            session,
            engine,
            body.ascii_armor,
            email=body.email,
            purpose=body.purpose,
            user_id=admin.id,
        )
    except CryptoError as exc:
        _raise(exc)
    return PgpImportOut(fingerprints=list(fps))


@router.post(
    "/smime-register", response_model=SmimeRegisterOut, status_code=status.HTTP_201_CREATED
)
async def admin_smime_register(
    body: SmimeRegisterIn,
    admin: AdminUser,
    session: DbSession,
    settings: AppSettings,
) -> SmimeRegisterOut:
    """Alias: add a certificate and/or its private key to the Znuny-layout store.

    ``email`` is informational only — the store files certificates by subject
    hash, the addresses come from the certificate itself.
    """
    if not body.cert_pem and not body.key_pem:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, detail="cert_pem or key_pem is required"
        )
    store = await _smime(session, settings)
    filename: str | None = None
    try:
        if body.cert_pem:
            entry = await ks.add_smime_certificate(
                session, store, _certificate_bytes(body.cert_pem), user_id=admin.id
            )
            filename = entry.filename
        if body.key_pem:
            entry, _gen = await ks.add_smime_private_key(
                session, store, body.key_pem.encode("utf-8"), body.secret, user_id=admin.id
            )
            filename = entry.filename
    except CryptoError as exc:
        _raise(exc)
    return SmimeRegisterOut(
        email=body.email,
        has_cert=body.cert_pem is not None,
        has_key=body.key_pem is not None,
        filename=filename,
    )
