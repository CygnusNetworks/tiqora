"""Customer PGP keys and S/MIME certificates (Znuny customer preference modules).

Ports ``Kernel::Output::HTML::Preferences::PGP`` / ``::SMIME`` as used by the
customer portal preferences (``CustomerPreferencesGroups###PGP`` / ``###SMIME``)
and the customer-user admin: the uploaded key goes into the **shared** key
store (gpg keyring, ``SMIME::CertPath``) and the customer's
``customer_preferences`` point at it, exactly the keys Znuny writes:

* PGP: ``PGPKeyID`` (the key id gpg reports on import) and ``PGPFilename``
  (``<uid>-<bits>-<key id>.pub``, Znuny's ``Identifier-Bit-Key.Type``);
* S/MIME: ``SMIMEHash``, ``SMIMEFingerprint``, ``SMIMEFilename`` (``<hash>.<n>``).

A customer's keys are the store entries carrying the customer's email
address, plus the one the preferences point at (a key for another address
uploaded by an agent). Encryption itself looks keys up by recipient address,
as Znuny does — the preferences only record the upload.

Deliberate tightening over Znuny (which accepts anything, secret keys
included): customer uploads take **public** material only, no CA
certificates, and a customer uploading for themself (portal) must upload a
key that carries their own email address — otherwise anyone with a portal
login could plant a key for somebody else's address. Deleting through the
customer paths only removes public entries linked to that customer; keys
with a secret part are left to the admin pages.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import pkcs7, pkcs12
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.crypto import CryptoError, CryptoNotFoundError
from tiqora.crypto import keystore as ks
from tiqora.crypto.pgp import PgpEngine, PgpKeyInfo
from tiqora.crypto.pgp import _emails as uid_emails
from tiqora.crypto.smime_store import SmimeEntry, SmimeStore, SmimeStoreError, validate_filename
from tiqora.db.legacy.customer import CustomerPreferences

PGP_PREF_KEYS: tuple[str, ...] = ("PGPKeyID", "PGPFilename")
SMIME_PREF_KEYS: tuple[str, ...] = ("SMIMEHash", "SMIMEFingerprint", "SMIMEFilename")


class CustomerKeyError(CryptoError):
    """A customer upload/delete was refused (message is user-facing)."""


# ------------------------------------------------------------ cert formats


def convert_cert_format(data: bytes, passphrase: str = "") -> bytes:
    """Any certificate container → PEM of the end-entity certificate.

    Znuny ``ConvertCertFormat``: PEM passes through, DER, PKCS#7 (``.p7b``,
    LDAP ``userSMIMECertificate``; PEM or DER) and PKCS#12 (``.pfx``, with
    *passphrase*, default empty) are converted. Containers with several
    certificates yield the first non-CA one (Znuny's openssl call reads the
    first). Private keys in a PKCS#12 are ignored.
    """
    raw = data.strip() if data.lstrip().startswith(b"-----BEGIN") else data
    certs: list[x509.Certificate] = []
    if b"-----BEGIN CERTIFICATE-----" in raw:
        try:
            certs = x509.load_pem_x509_certificates(raw)
        except ValueError:
            certs = []
    if not certs and b"-----BEGIN PKCS7-----" in raw:
        try:
            certs = pkcs7.load_pem_pkcs7_certificates(raw)
        except ValueError:
            certs = []
    if not certs and not raw.startswith(b"-----BEGIN"):
        for loader in (_load_der, _load_der_pkcs7):
            certs = loader(raw)
            if certs:
                break
        if not certs:
            try:
                _key, cert, extra = pkcs12.load_key_and_certificates(
                    raw, passphrase.encode("utf-8") if passphrase else None
                )
            except (ValueError, TypeError):
                cert, extra = None, []
            certs = ([cert] if cert is not None else []) + list(extra or [])
    if not certs:
        raise SmimeStoreError(
            "Certificate could not be read, passphrase is invalid or file is corrupted"
        )
    chosen = next((c for c in certs if not _is_ca(c)), certs[0])
    return chosen.public_bytes(serialization.Encoding.PEM)


def _load_der(raw: bytes) -> list[x509.Certificate]:
    try:
        return [x509.load_der_x509_certificate(raw)]
    except ValueError:
        return []


def _load_der_pkcs7(raw: bytes) -> list[x509.Certificate]:
    try:
        return pkcs7.load_der_pkcs7_certificates(raw)
    except ValueError:
        return []


def _is_ca(cert: x509.Certificate) -> bool:
    try:
        return bool(cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca)
    except x509.ExtensionNotFound:
        return False


# ------------------------------------------------------------- preferences


async def get_preferences(session: AsyncSession, login: str) -> dict[str, str]:
    rows = (
        await session.execute(
            select(
                CustomerPreferences.preferences_key, CustomerPreferences.preferences_value
            ).where(CustomerPreferences.user_id == login)
        )
    ).all()
    return {str(k): str(v or "") for k, v in rows}


async def set_preferences(session: AsyncSession, login: str, values: dict[str, str]) -> None:
    """Znuny ``CustomerUser::SetPreferences``: delete + insert per key (no commit)."""
    for key, value in values.items():
        await session.execute(
            delete(CustomerPreferences).where(
                CustomerPreferences.user_id == login, CustomerPreferences.preferences_key == key
            )
        )
        session.add(
            CustomerPreferences(user_id=login, preferences_key=key, preferences_value=value[:250])
        )
    await session.flush()


# ----------------------------------------------------------------- listing


@dataclass
class CustomerKeys:
    pgp: list[PgpKeyInfo] = field(default_factory=list)
    smime: list[SmimeEntry] = field(default_factory=list)
    preferences: dict[str, str] = field(default_factory=dict)


def _emails(email: str) -> set[str]:
    return {e.strip().lower() for e in (email or "").split(",") if e.strip()}


def _pgp_linked(key: PgpKeyInfo, emails: set[str], pref_key_id: str) -> bool:
    return bool(emails & set(key.emails)) or bool(pref_key_id and key.matches(pref_key_id))


def _smime_linked(entry: SmimeEntry, emails: set[str], prefs: dict[str, str]) -> bool:
    if entry.filename == prefs.get("SMIMEFilename"):
        return True
    return entry.info is not None and bool(emails & set(entry.info.emails))


def customer_pgp_keys(engine: PgpEngine, email: str, prefs: dict[str, str]) -> list[PgpKeyInfo]:
    emails = _emails(email)
    pref = prefs.get("PGPKeyID", "")
    return [k for k in engine.list_keys() if _pgp_linked(k, emails, pref)]


def customer_smime_certs(store: SmimeStore, email: str, prefs: dict[str, str]) -> list[SmimeEntry]:
    emails = _emails(email)
    return [e for e in store.list_entries() if _smime_linked(e, emails, prefs)]


# ----------------------------------------------------------------- PGP


def _inspect_pgp(engine: PgpEngine, armored: str) -> list[dict[str, object]]:
    """Keys in *armored* without importing them (``gpg --import-options show-only``)."""
    gpg = engine._gpg()  # noqa: SLF001 — same engine, read-only scan
    return [dict(k) for k in gpg.scan_keys_mem(armored)]


async def add_customer_pgp_key(
    session: AsyncSession,
    engine: PgpEngine,
    *,
    login: str,
    email: str,
    armored: str,
    user_id: int | None,
    require_own_email: bool,
) -> PgpKeyInfo:
    """Import a customer's public key and point ``PGPKeyID``/``PGPFilename`` at it."""
    if "PRIVATE KEY BLOCK" in armored:
        raise CustomerKeyError("Only public keys can be uploaded here")
    scanned = await asyncio.to_thread(_inspect_pgp, engine, armored)
    if not scanned:
        raise CustomerKeyError("No PGP public key found in the upload")
    if len(scanned) > 1:
        raise CustomerKeyError("Upload exactly one PGP public key")
    if require_own_email:
        uids = [str(u) for u in scanned[0].get("uids") or []]  # type: ignore[attr-defined]
        if not (_emails(email) & set(uid_emails(uids))):
            raise CustomerKeyError("The key does not carry your email address")
    fingerprints = await asyncio.to_thread(engine.import_key, armored)
    key = await asyncio.to_thread(engine.find_key, fingerprints[0])
    if key is None:  # pragma: no cover — gpg just imported it
        raise CryptoNotFoundError("imported key not found in the keyring")
    uid = key.uids[0] if key.uids else key.key_id
    await set_preferences(
        session,
        login,
        {
            "PGPKeyID": key.key_id,
            "PGPFilename": f"{uid}-{key.bits or 0}-{key.znuny_key_id}.pub",
        },
    )
    await ks.record_audit(
        session,
        key_type="pgp",
        identifier=key.fingerprint,
        action="import",
        user_id=user_id,
        email=", ".join(key.emails) or email,
        detail=f"customer {login}",
    )
    await session.commit()
    return key


async def delete_customer_pgp_key(
    session: AsyncSession,
    engine: PgpEngine,
    *,
    login: str,
    email: str,
    key_ref: str,
    user_id: int | None,
) -> None:
    prefs = await get_preferences(session, login)
    key = await asyncio.to_thread(engine.find_key, key_ref)
    if key is None or not _pgp_linked(key, _emails(email), prefs.get("PGPKeyID", "")):
        raise CryptoNotFoundError(f"no PGP key {key_ref} for this customer")
    if key.has_secret:
        raise CustomerKeyError("Keys with a secret part are managed on the admin PGP page")
    await asyncio.to_thread(engine.delete_key, key.fingerprint, secret_only=False)
    if prefs.get("PGPKeyID") and key.matches(prefs["PGPKeyID"]):
        await set_preferences(session, login, dict.fromkeys(PGP_PREF_KEYS, ""))
    await ks.record_audit(
        session,
        key_type="pgp",
        identifier=key.fingerprint,
        action="delete",
        user_id=user_id,
        email=", ".join(key.emails) or None,
        detail=f"customer {login}",
    )
    await session.commit()


# ---------------------------------------------------------------- S/MIME


async def add_customer_smime_certificate(
    session: AsyncSession,
    store: SmimeStore,
    *,
    login: str,
    email: str,
    data: bytes,
    user_id: int | None,
    require_own_email: bool,
) -> SmimeEntry:
    """Store a customer's certificate and set ``SMIMEHash``/``Fingerprint``/``Filename``.

    A certificate that is already stored (same fingerprint) is linked, not
    refused — re-uploading your own certificate is not an error for a customer.
    """
    pem = await asyncio.to_thread(convert_cert_format, data)
    info = await asyncio.to_thread(store.cert_attributes, pem)
    if info.is_ca:
        raise CustomerKeyError("CA certificates are managed on the admin S/MIME page")
    if require_own_email and not (_emails(email) & set(info.emails)):
        raise CustomerKeyError("The certificate does not carry your email address")
    existing = await asyncio.to_thread(store.find_by_fingerprint, info.fingerprint)
    if existing is not None:
        entry = existing
    else:
        entry = await ks.add_smime_certificate(session, store, pem, user_id=user_id or 1)
    await set_preferences(
        session,
        login,
        {
            "SMIMEHash": info.hash,
            "SMIMEFingerprint": info.fingerprint,
            "SMIMEFilename": entry.filename,
        },
    )
    await ks.record_audit(
        session,
        key_type="smime",
        identifier=entry.filename,
        action="customer_link",
        user_id=user_id,
        email=info.email_joined,
        detail=f"customer {login}",
    )
    await session.commit()
    return entry


async def delete_customer_smime_certificate(
    session: AsyncSession,
    store: SmimeStore,
    *,
    login: str,
    email: str,
    filename: str,
    user_id: int | None,
) -> dict[str, str]:
    validate_filename(filename)
    prefs = await get_preferences(session, login)
    entry = await asyncio.to_thread(store.entry, filename)
    if not _smime_linked(entry, _emails(email), prefs):
        raise CryptoNotFoundError(f"no certificate {filename} for this customer")
    if entry.has_private:
        raise CustomerKeyError(
            "Certificates with a private key are managed on the admin S/MIME page"
        )
    # keystore.delete_smime also clears SMIMEFilename/Hash/Fingerprint of
    # every customer pointing at the file (Znuny AdminSMIME Delete).
    return await ks.delete_smime(session, store, filename, private_only=False, user_id=user_id or 1)


__all__ = [
    "PGP_PREF_KEYS",
    "SMIME_PREF_KEYS",
    "CustomerKeyError",
    "CustomerKeys",
    "add_customer_pgp_key",
    "add_customer_smime_certificate",
    "convert_cert_format",
    "customer_pgp_keys",
    "customer_smime_certs",
    "delete_customer_pgp_key",
    "delete_customer_smime_certificate",
    "get_preferences",
    "set_preferences",
]
