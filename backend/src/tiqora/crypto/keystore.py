"""Key-store service layer: PGP keyring + Znuny-layout S/MIME store + audit trail.

The admin API and the ``tiqora crypto`` CLI go through these functions so
every mutation (a) touches the key material, (b) keeps Znuny's DB index
(``smime_keys``, ``smime_signer_cert_relations``) in sync and (c) writes a
:class:`~tiqora.db.tiqora.models.TiqoraCryptoKey` audit row. Secrets never
leave this layer.

Blocking gpg/openssl work runs in a thread (``asyncio.to_thread``).
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.crypto import CryptoError, CryptoUnavailableError
from tiqora.crypto import smime_index as idx
from tiqora.crypto.config import CryptoConfig
from tiqora.crypto.pgp import PgpEngine, PgpKeyInfo
from tiqora.crypto.smime_store import SmimeEntry, SmimeStore, SmimeStoreError, validate_filename
from tiqora.db.tiqora.models import TiqoraCryptoKey


async def record_audit(
    session: AsyncSession,
    *,
    key_type: str,
    identifier: str,
    action: str,
    user_id: int | None = None,
    email: str | None = None,
    has_private_key: bool = False,
    detail: str | None = None,
    purpose: str = "both",
) -> None:
    session.add(
        TiqoraCryptoKey(
            key_type=key_type,
            identifier=identifier[:255],
            email=(email or None) and email[:255],
            purpose=purpose,
            has_private_key=has_private_key,
            action=action,
            user_id=user_id,
            detail=(detail or None) and detail[:500],
        )
    )


# ---------------------------------------------------------------------- PGP


async def import_pgp_key(
    session: AsyncSession,
    engine: PgpEngine,
    key_data: str,
    *,
    email: str | None = None,
    purpose: str = "both",
    user_id: int | None = None,
) -> list[str]:
    """Import an armored key into the keyring and record one audit row per fingerprint."""
    fingerprints = await asyncio.to_thread(engine.import_key, key_data)
    has_private = "-----BEGIN PGP PRIVATE KEY BLOCK-----" in key_data
    for fp in fingerprints:
        await record_audit(
            session,
            key_type="pgp",
            identifier=fp,
            action="import",
            user_id=user_id,
            email=email,
            has_private_key=has_private,
            purpose=purpose,
        )
    await session.commit()
    return fingerprints


async def delete_pgp_key(
    session: AsyncSession,
    engine: PgpEngine,
    key_ref: str,
    *,
    secret_only: bool,
    user_id: int | None,
) -> PgpKeyInfo:
    key = await asyncio.to_thread(engine.delete_key, key_ref, secret_only=secret_only)
    await record_audit(
        session,
        key_type="pgp",
        identifier=key.fingerprint,
        action="delete_secret" if secret_only else "delete",
        user_id=user_id,
        email=", ".join(key.emails) or None,
        has_private_key=key.has_secret and secret_only is False,
    )
    await session.commit()
    return key


# ------------------------------------------------------------------- S/MIME


async def add_smime_certificate(
    session: AsyncSession, store: SmimeStore, data: bytes, *, user_id: int
) -> SmimeEntry:
    entry = await asyncio.to_thread(store.add_certificate, data)
    assert entry.info is not None
    await idx.index_add(session, entry.filename, entry.info, "cert", user_id)
    await record_audit(
        session,
        key_type="smime",
        identifier=entry.filename,
        action="add_certificate",
        user_id=user_id,
        email=entry.info.email_joined,
        detail=entry.info.fingerprint,
    )
    await session.commit()
    return entry


async def add_smime_private_key(
    session: AsyncSession, store: SmimeStore, key: bytes, secret: str, *, user_id: int
) -> tuple[SmimeEntry, bool]:
    entry, generated = await asyncio.to_thread(store.add_private_key, key, secret)
    assert entry.info is not None
    await idx.index_add(session, entry.filename, entry.info, "P", user_id)
    await record_audit(
        session,
        key_type="smime",
        identifier=entry.filename,
        action="add_private",
        user_id=user_id,
        email=entry.info.email_joined,
        has_private_key=True,
        detail="secret generated" if generated else None,
    )
    await session.commit()
    return entry, generated


async def delete_smime(
    session: AsyncSession,
    store: SmimeStore,
    filename: str,
    *,
    private_only: bool,
    user_id: int,
) -> dict[str, str]:
    """Znuny AdminSMIME ``Delete``: private key only, or certificate (+ key, relations).

    Returns the index-compaction renames (``old → new`` filenames).
    """
    validate_filename(filename)
    entry = await asyncio.to_thread(store.entry, filename)
    fp = entry.info.fingerprint if entry.info else None
    renames: dict[str, str] = {}
    if private_only:
        removed = await asyncio.to_thread(store.remove_private, filename)
        if not removed:
            raise SmimeStoreError(f"{filename} has no private key")
        await idx.index_remove(session, filename, "P")
        if fp:
            await idx.relations_delete(session, cert_fp=fp)
    else:
        renames = await asyncio.to_thread(store.remove_certificate, filename)
        await idx.index_remove(session, filename)
        if fp:
            await idx.relations_delete(session, ca_fp=fp)
            await idx.relations_delete(session, cert_fp=fp)
        await idx.reset_customer_preferences(session, filename)
        await idx.index_apply_renames(session, renames)
    await record_audit(
        session,
        key_type="smime",
        identifier=filename,
        action="delete_private" if private_only else "delete",
        user_id=user_id,
        email=entry.info.email_joined if entry.info else None,
        has_private_key=entry.has_private,
        detail=(
            ("renamed " + ", ".join(f"{a}→{b}" for a, b in renames.items())) if renames else fp
        ),
    )
    await session.commit()
    return renames


@dataclass(frozen=True)
class RelationView:
    ca_filename: str | None  # None: CA certificate no longer in the store
    ca_fingerprint: str
    ca_subject: str | None
    ca_not_after: datetime | None
    created: datetime | None


async def list_smime_relations(
    session: AsyncSession, store: SmimeStore, filename: str
) -> list[RelationView]:
    entry = await asyncio.to_thread(store.entry, validate_filename(filename))
    if entry.info is None:
        raise SmimeStoreError(f"{filename} is not a valid certificate")
    rows = await idx.relations_for(session, entry.info.fingerprint)
    certs = await asyncio.to_thread(store.list_entries)
    by_fp = {e.info.fingerprint: e for e in certs if e.info is not None}
    out: list[RelationView] = []
    for row in rows:
        ca = by_fp.get(row.ca_fingerprint)
        out.append(
            RelationView(
                ca_filename=ca.filename if ca else None,
                ca_fingerprint=row.ca_fingerprint,
                ca_subject=ca.info.subject if ca and ca.info else None,
                ca_not_after=ca.info.not_after if ca and ca.info else None,
                created=row.created,
            )
        )
    return out


async def add_smime_relation(
    session: AsyncSession, store: SmimeStore, filename: str, ca_filename: str, *, user_id: int
) -> None:
    """Znuny ``SignerCertRelationAdd``: the signer needs a private key, the CA a certificate."""
    signer = await asyncio.to_thread(store.entry, validate_filename(filename))
    ca = await asyncio.to_thread(store.entry, validate_filename(ca_filename))
    if signer.info is None or ca.info is None:
        raise SmimeStoreError("both certificates must be valid")
    if not signer.has_private:
        raise SmimeStoreError(
            f"{filename} has no private key — relations are for signing certificates"
        )
    if signer.info.fingerprint == ca.info.fingerprint:
        raise SmimeStoreError("CA fingerprint must be different from the certificate fingerprint")
    if await idx.relation_exists(session, signer.info.fingerprint, ca.info.fingerprint):
        raise SmimeStoreError("relation exists")
    await idx.relation_add(session, signer.info, ca.info, user_id)
    await record_audit(
        session,
        key_type="smime",
        identifier=filename,
        action="relation_add",
        user_id=user_id,
        detail=f"ca={ca_filename} {ca.info.fingerprint}",
    )
    await session.commit()


async def delete_smime_relation(
    session: AsyncSession,
    store: SmimeStore,
    filename: str,
    ca_fingerprint: str,
    *,
    user_id: int,
) -> None:
    signer = await asyncio.to_thread(store.entry, validate_filename(filename))
    if signer.info is None:
        raise SmimeStoreError(f"{filename} is not a valid certificate")
    removed = await idx.relations_delete(
        session, cert_fp=signer.info.fingerprint, ca_fp=ca_fingerprint.upper()
    )
    if not removed:
        raise SmimeStoreError("relation doesn't exist")
    await record_audit(
        session,
        key_type="smime",
        identifier=filename,
        action="relation_delete",
        user_id=user_id,
        detail=f"ca={ca_fingerprint}",
    )
    await session.commit()


async def rehash_smime_store(
    session: AsyncSession, store: SmimeStore, *, user_id: int = 1
) -> tuple[dict[str, str], int]:
    """Rehash file names, then re-sync ``smime_keys`` with the directory."""
    renames = await asyncio.to_thread(store.rehash)
    await idx.index_apply_renames(session, renames)
    entries = await asyncio.to_thread(store.list_entries)
    changes = await idx.reindex(session, entries, user_id)
    if renames or changes:
        await record_audit(
            session,
            key_type="smime",
            identifier="*",
            action="rehash",
            user_id=user_id,
            detail=f"{len(renames)} renamed, {changes} index rows changed",
        )
    await session.commit()
    return renames, changes


# ------------------------------------------------- flat store → Znuny layout


@dataclass
class FlatMigrationReport:
    migrated: list[str]  # "<email>.crt → <hash>.<n>" lines
    skipped: list[str]  # already present in the store
    errors: list[str]
    removed_files: list[str]


async def migrate_flat_store(
    session: AsyncSession,
    store: SmimeStore,
    cert_dir: str,
    private_dir: str,
    *,
    secret: str = "",
    keep: bool = False,
    dry_run: bool = False,
    user_id: int = 1,
) -> FlatMigrationReport:
    """Move the pre-B1 Tiqora ``<email>.crt`` / ``<email>.key`` files into the Znuny layout.

    Certificates are added (a duplicate counts as already migrated), then the
    matching key with *secret* — an unencrypted key without secret gets a
    generated one, so the result is Znuny-readable. Flat files are deleted
    once both parts are in the store, unless *keep*.
    """
    report = FlatMigrationReport([], [], [], [])
    cdir = Path(cert_dir) if cert_dir else None
    pdir = Path(private_dir) if private_dir else None
    if cdir is None or not cdir.is_dir():
        return report
    for crt in sorted(cdir.glob("*.crt")):
        key_file = pdir / f"{crt.stem}.key" if pdir is not None else None
        data = crt.read_bytes()
        if dry_run:
            info = await asyncio.to_thread(store.cert_attributes, data)
            report.migrated.append(f"{crt.name} → {info.hash}.<n> (dry run)")
            continue
        try:
            entry = await add_smime_certificate(session, store, data, user_id=user_id)
            report.migrated.append(f"{crt.name} → {entry.filename}")
        except SmimeStoreError as exc:
            if "already installed" not in str(exc):
                report.errors.append(f"{crt.name}: {exc}")
                continue
            report.skipped.append(f"{crt.name}: {exc}")
        key_ok = True
        if key_file is not None and key_file.is_file():
            try:
                entry, _generated = await add_smime_private_key(
                    session, store, key_file.read_bytes(), secret, user_id=user_id
                )
                report.migrated.append(f"{key_file.name} → {entry.filename} (private)")
            except CryptoError as exc:
                key_ok = False
                report.errors.append(f"{key_file.name}: {exc}")
        if keep or not key_ok:
            continue
        crt.unlink()
        report.removed_files.append(str(crt))
        if key_file is not None and key_file.is_file():
            key_file.unlink()
            report.removed_files.append(str(key_file))
    if report.migrated and not dry_run:
        await record_audit(
            session,
            key_type="smime",
            identifier="*",
            action="migrate",
            user_id=user_id,
            detail=f"{len(report.migrated)} migrated, {len(report.errors)} errors",
        )
        await session.commit()
    return report


# ------------------------------------------------------------ sign-key picker

SIGN_KEY_RE = re.compile(
    r"^(?:PGP::(?P<pgp_method>Inline|Detached)::(?P<pgp_key>[0-9A-Fa-f]{8,40})"
    r"|SMIME::Detached::(?P<smime_file>[0-9a-f]{8}\.(?:[0-9]|[1-9][0-9])))$"
)


@dataclass(frozen=True)
class SignKeyOption:
    value: str  # Znuny queue.default_sign_key format
    backend: str  # "PGP" | "SMIME"
    method: str  # "Inline" | "Detached"
    key: str  # PGP key id / S/MIME filename
    label: str
    status: str  # PGP: good|expired|revoked; S/MIME: valid|expired
    expires: datetime | None
    emails: list[str]


def smime_status(entry: SmimeEntry) -> str:
    if entry.info is None:
        return "invalid"
    return "expired" if entry.info.is_expired() else "valid"


def _pgp_options(engine: PgpEngine, email: str | None) -> list[SignKeyOption]:
    keys = (
        engine.search(email, secret=True)
        if email
        else [k for k in engine.list_keys() if k.has_secret]
    )
    out: list[SignKeyOption] = []
    for key in keys:
        ident = key.znuny_key_id
        uid = key.uids[0] if key.uids else ""
        for method in ("Detached", "Inline"):
            out.append(
                SignKeyOption(
                    value=f"PGP::{method}::{ident}",
                    backend="PGP",
                    method=method,
                    key=ident,
                    label=f"PGP-{method}: [{key.status}] {ident} {uid}".strip(),
                    status=key.status,
                    expires=key.expires,
                    emails=key.emails,
                )
            )
    return out


def _smime_options(store: SmimeStore, email: str | None) -> list[SignKeyOption]:
    entries = (
        store.search(email, private=True)
        if email
        else [e for e in store.list_entries() if e.has_private]
    )
    out: list[SignKeyOption] = []
    for entry in entries:
        if entry.info is None:
            continue
        status = smime_status(entry)
        out.append(
            SignKeyOption(
                value=f"SMIME::Detached::{entry.filename}",
                backend="SMIME",
                method="Detached",
                key=entry.filename,
                label=(
                    f"SMIME-Detached: [{status}] {entry.filename} "
                    f"[{entry.info.short_end_date.isoformat()}] {entry.info.email_joined}"
                ),
                status=status,
                expires=entry.info.not_after,
                emails=entry.info.emails,
            )
        )
    return out


def sign_key_options_sync(config: CryptoConfig, *, email: str | None = None) -> list[SignKeyOption]:
    """Queue default-sign-key choices (Znuny ``AdminQueue`` ``DefaultSignKeyList``).

    Only enabled, usable backends contribute; an unusable backend yields no
    options instead of an error so the queue form still works.
    """
    options: list[SignKeyOption] = []
    if config.pgp.enabled and config.pgp.homedir:
        with contextlib.suppress(CryptoError):
            options.extend(_pgp_options(PgpEngine.from_config(config.pgp), email))
    if config.smime.enabled and config.smime.cert_path:
        with contextlib.suppress(CryptoError):
            options.extend(_smime_options(SmimeStore.from_config(config.smime), email))
    return options


async def sign_key_options(
    config: CryptoConfig, *, email: str | None = None
) -> list[SignKeyOption]:
    return await asyncio.to_thread(sign_key_options_sync, config, email=email)


def _pgp_key_exists(config: CryptoConfig, key: str) -> bool:
    found = PgpEngine.from_config(config.pgp).find_key(key)
    return found is not None and found.has_secret


def _smime_key_exists(config: CryptoConfig, filename: str) -> bool:
    try:
        entry = SmimeStore.from_config(config.smime).entry(filename)
    except SmimeStoreError:
        return False
    return entry.has_private


async def validate_sign_key(config: CryptoConfig, value: str | None) -> str | None:
    """Return an error message for an unusable ``default_sign_key`` value, else ``None``."""
    if not value:
        return None
    m = SIGN_KEY_RE.match(value)
    if m is None:
        return (
            "default_sign_key must be PGP::Inline::<keyid>, PGP::Detached::<keyid> "
            "or SMIME::Detached::<hash>.<n>"
        )
    if m.group("pgp_key"):
        if not (config.pgp.enabled and config.pgp.homedir):
            return "PGP is not enabled/configured"
        try:
            ok = await asyncio.to_thread(_pgp_key_exists, config, m.group("pgp_key"))
        except CryptoUnavailableError as exc:
            return f"PGP unavailable: {exc}"
        return None if ok else f"no PGP secret key {m.group('pgp_key')}"
    if not (config.smime.enabled and config.smime.cert_path):
        return "S/MIME is not enabled/configured"
    try:
        ok = await asyncio.to_thread(_smime_key_exists, config, m.group("smime_file"))
    except CryptoUnavailableError as exc:
        return f"S/MIME unavailable: {exc}"
    return None if ok else f"no S/MIME private key {m.group('smime_file')}"


__all__ = [
    "SIGN_KEY_RE",
    "RelationView",
    "SignKeyOption",
    "add_smime_certificate",
    "add_smime_private_key",
    "add_smime_relation",
    "delete_pgp_key",
    "delete_smime",
    "delete_smime_relation",
    "FlatMigrationReport",
    "import_pgp_key",
    "migrate_flat_store",
    "list_smime_relations",
    "record_audit",
    "rehash_smime_store",
    "sign_key_options",
    "sign_key_options_sync",
    "smime_status",
    "validate_sign_key",
]
