"""Admin API for PGP/S-MIME key audit trail + import (material via keystore)."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from tiqora.api.deps import AppSettings, DbSession
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.crypto import CryptoError
from tiqora.crypto.keystore import SmimeKeyStore, import_pgp_key, register_smime_key
from tiqora.crypto.pgp import PgpEngine
from tiqora.db.tiqora.models import TiqoraCryptoKey

router = APIRouter(prefix="/crypto-keys", tags=["admin:crypto"])


class CryptoKeyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    key_type: str
    identifier: str
    email: str | None = None
    purpose: str
    has_private_key: bool
    created: datetime | None = None


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
    purpose: str = "both"


class SmimeRegisterOut(BaseModel):
    email: str
    has_cert: bool
    has_key: bool


@router.get("", response_model=list[CryptoKeyOut])
async def list_crypto_keys(admin: AdminUser, session: DbSession) -> list[TiqoraCryptoKey]:
    """List audit rows for imported crypto keys (not key material)."""
    _ = admin
    rows = (
        await session.execute(select(TiqoraCryptoKey).order_by(TiqoraCryptoKey.id.desc()))
    ).scalars()
    return list(rows)


@router.post("/pgp-import", response_model=PgpImportOut, status_code=status.HTTP_201_CREATED)
async def admin_pgp_import(
    body: PgpImportIn,
    admin: AdminUser,
    session: DbSession,
    settings: AppSettings,
) -> PgpImportOut:
    """Import a PGP key into the configured keyring + write audit rows."""
    _ = admin
    if not getattr(settings, "crypto_pgp_enabled", False) and not getattr(
        settings, "crypto_pgp_gnupg_home", None
    ):
        # Still try — engine may work if gpg home is set via env defaults.
        pass
    try:
        if not settings.crypto_pgp_gnupghome:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="TIQORA_CRYPTO_PGP_GNUPGHOME is not configured",
            )
        engine = PgpEngine(settings.crypto_pgp_gnupghome)
        fps = await import_pgp_key(
            session,
            engine,
            body.ascii_armor,
            email=body.email,
            purpose=body.purpose,
        )
        return PgpImportOut(fingerprints=list(fps))
    except CryptoError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"PGP import unavailable: {exc}",
        ) from exc


@router.post(
    "/smime-register", response_model=SmimeRegisterOut, status_code=status.HTTP_201_CREATED
)
async def admin_smime_register(
    body: SmimeRegisterIn,
    admin: AdminUser,
    session: DbSession,
    settings: AppSettings,
) -> SmimeRegisterOut:
    """Write S/MIME cert/key files under configured dirs + audit row."""
    _ = admin
    cert_dir = settings.crypto_smime_cert_dir or ""
    private_dir = settings.crypto_smime_private_dir or ""
    if not cert_dir and not private_dir:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="S/MIME cert/private directories are not configured",
        )
    try:
        store = SmimeKeyStore(cert_dir=cert_dir, private_dir=private_dir)
        paths = await register_smime_key(
            session,
            store,
            body.email,
            cert_pem=body.cert_pem.encode("utf-8") if body.cert_pem else None,
            key_pem=body.key_pem.encode("utf-8") if body.key_pem else None,
            purpose=body.purpose,
        )
        return SmimeRegisterOut(
            email=body.email,
            has_cert=paths.cert_path is not None,
            has_key=paths.key_path is not None,
        )
    except CryptoError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"S/MIME register unavailable: {exc}",
        ) from exc