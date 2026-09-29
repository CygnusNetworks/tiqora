"""DB half of the Znuny-compatible S/MIME store: ``smime_keys`` + signer relations.

Znuny lists certificates from ``smime_keys`` (not from the directory), so every
file Tiqora adds/removes/renames in :class:`~tiqora.crypto.smime_store.SmimeStore`
is mirrored here exactly as ``Kernel::System::Crypt::SMIME`` would write it:
``key_type`` ``cert`` / ``P``, ``key_hash``, ``file_name``, sorted comma-joined
``email_address``, ``expiration_date`` (date of notAfter), SHA1
``fingerprint`` and Znuny's ``subject`` string.

Signer relations (``smime_signer_cert_relations``) attach CA certificates to
a signer's signatures (``openssl smime -sign -certfile``); both sides are
identified by fingerprint, so file renames do not affect them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, inspect, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.crypto.smime_store import SmimeCertInfo, SmimeEntry, SmimeStore
from tiqora.db.legacy.crypto import SmimeKey, SmimeSignerCertRelation


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None, microsecond=0)


async def has_smime_keys_table(session: AsyncSession) -> bool:
    """``smime_keys`` only exists from Znuny 6.4 on."""

    def _check(sync_session: object) -> bool:
        conn = sync_session.connection()  # type: ignore[attr-defined]
        return bool(inspect(conn).has_table("smime_keys"))

    return bool(await session.run_sync(_check))


async def index_add(
    session: AsyncSession, filename: str, info: SmimeCertInfo, key_type: str, user_id: int
) -> None:
    """Insert (or refresh) the ``smime_keys`` row for a cert (``cert``) or key (``P``)."""
    if not await has_smime_keys_table(session):
        return
    await session.execute(
        delete(SmimeKey).where(SmimeKey.file_name == filename, SmimeKey.key_type == key_type)
    )
    now = _now()
    session.add(
        SmimeKey(
            key_hash=info.hash,
            key_type=key_type,
            file_name=filename,
            email_address=info.email_joined,
            expiration_date=datetime.combine(info.short_end_date, datetime.min.time()),
            fingerprint=info.fingerprint,
            subject=info.znuny_subject,
            create_time=now,
            change_time=now,
            create_by=user_id,
            change_by=user_id,
        )
    )


async def index_remove(session: AsyncSession, filename: str, key_type: str | None = None) -> None:
    if not await has_smime_keys_table(session):
        return
    stmt = delete(SmimeKey).where(SmimeKey.file_name == filename)
    if key_type is not None:
        stmt = stmt.where(SmimeKey.key_type == key_type)
    await session.execute(stmt)


async def index_apply_renames(session: AsyncSession, renames: dict[str, str]) -> None:
    """Follow store renames in ``smime_keys`` and customer ``SMIMEFilename`` preferences."""
    if not renames:
        return
    has_index = await has_smime_keys_table(session)
    # Two phases so a chain (a→b, b→c) never collides mid-way.
    for old in renames:
        tmp = f"~{old}"
        if has_index:
            await session.execute(
                update(SmimeKey).where(SmimeKey.file_name == old).values(file_name=tmp)
            )
        await session.execute(
            text(
                "UPDATE customer_preferences SET preferences_value = :tmp"
                " WHERE preferences_key = 'SMIMEFilename' AND preferences_value = :old"
            ),
            {"tmp": tmp, "old": old},
        )
    for old, new in renames.items():
        tmp = f"~{old}"
        if has_index:
            await session.execute(
                update(SmimeKey)
                .where(SmimeKey.file_name == tmp)
                .values(file_name=new, key_hash=new.split(".", 1)[0], change_time=_now())
            )
        await session.execute(
            text(
                "UPDATE customer_preferences SET preferences_value = :new"
                " WHERE preferences_key = 'SMIMEFilename' AND preferences_value = :tmp"
            ),
            {"tmp": tmp, "new": new},
        )


async def reset_customer_preferences(session: AsyncSession, filename: str) -> None:
    """Znuny AdminSMIME ``Delete``: clear customers' SMIME preferences for a removed cert."""
    rows = (
        await session.execute(
            text(
                "SELECT user_id FROM customer_preferences"
                " WHERE preferences_key = 'SMIMEFilename' AND preferences_value = :f"
            ),
            {"f": filename},
        )
    ).all()
    for (user_id,) in rows:
        await session.execute(
            text(
                "UPDATE customer_preferences SET preferences_value = ''"
                " WHERE user_id = :u AND preferences_key IN"
                " ('SMIMEHash', 'SMIMEFingerprint', 'SMIMEFilename')"
            ),
            {"u": user_id},
        )


async def reindex(session: AsyncSession, entries: list[SmimeEntry], user_id: int = 1) -> int:
    """Make ``smime_keys`` match the files on disk (Znuny ``ReIndexCertificate``/``-Private``).

    Returns the number of rows written or removed.
    """
    if not await has_smime_keys_table(session):
        return 0
    rows = (await session.execute(select(SmimeKey))).scalars().all()
    existing = {(r.file_name, r.key_type): r for r in rows}
    wanted: dict[tuple[str, str], SmimeEntry] = {}
    for entry in entries:
        if entry.info is None:
            continue
        wanted[(entry.filename, "cert")] = entry
        if entry.has_private:
            wanted[(entry.filename, "P")] = entry
    changes = 0
    for key, row in existing.items():
        want = wanted.get(key)
        if want is None or want.info is None or row.fingerprint != want.info.fingerprint:
            await session.delete(row)
            changes += 1
    for key, entry in wanted.items():
        have = existing.get(key)
        if (
            have is not None
            and entry.info is not None
            and have.fingerprint == entry.info.fingerprint
        ):
            continue
        assert entry.info is not None
        await index_add(session, entry.filename, entry.info, key[1], user_id)
        changes += 1
    return changes


@dataclass(frozen=True)
class RelationRow:
    id: int
    cert_hash: str
    cert_fingerprint: str
    ca_hash: str
    ca_fingerprint: str
    created: datetime | None
    created_by: int | None


async def relations_for(session: AsyncSession, cert_fingerprint: str) -> list[RelationRow]:
    rows = (
        (
            await session.execute(
                select(SmimeSignerCertRelation)
                .where(SmimeSignerCertRelation.cert_fingerprint == cert_fingerprint)
                .order_by(SmimeSignerCertRelation.id.desc())
            )
        )
        .scalars()
        .all()
    )
    return [
        RelationRow(
            id=r.id,
            cert_hash=r.cert_hash,
            cert_fingerprint=r.cert_fingerprint,
            ca_hash=r.ca_hash,
            ca_fingerprint=r.ca_fingerprint,
            created=r.create_time,
            created_by=r.create_by,
        )
        for r in rows
    ]


async def relation_exists(session: AsyncSession, cert_fp: str, ca_fp: str) -> bool:
    row = (
        await session.execute(
            select(SmimeSignerCertRelation.id).where(
                SmimeSignerCertRelation.cert_fingerprint == cert_fp,
                SmimeSignerCertRelation.ca_fingerprint == ca_fp,
            )
        )
    ).first()
    return row is not None


async def relation_add(
    session: AsyncSession, cert: SmimeCertInfo, ca: SmimeCertInfo, user_id: int
) -> None:
    now = _now()
    session.add(
        SmimeSignerCertRelation(
            cert_hash=cert.hash,
            cert_fingerprint=cert.fingerprint,
            ca_hash=ca.hash,
            ca_fingerprint=ca.fingerprint,
            create_time=now,
            create_by=user_id,
            change_time=now,
            change_by=user_id,
        )
    )


async def relations_delete(
    session: AsyncSession, *, cert_fp: str | None = None, ca_fp: str | None = None
) -> int:
    """Znuny ``SignerCertRelationDelete`` — by cert, by CA, or the exact pair."""
    if cert_fp is None and ca_fp is None:
        return 0
    stmt = delete(SmimeSignerCertRelation)
    if cert_fp is not None:
        stmt = stmt.where(SmimeSignerCertRelation.cert_fingerprint == cert_fp)
    if ca_fp is not None:
        stmt = stmt.where(SmimeSignerCertRelation.ca_fingerprint == ca_fp)
    result = await session.execute(stmt)
    return int(getattr(result, "rowcount", 0) or 0)


def related_certificate_paths(store: SmimeStore, relations: list[RelationRow]) -> list[str]:
    """CA certificate file paths for ``openssl smime -sign -certfile`` (used by B3)."""
    paths: list[str] = []
    for rel in relations:
        entry = store.find_by_fingerprint(rel.ca_fingerprint)
        if entry is not None:
            paths.append(str(store.cert_dir / entry.filename))
    return paths


__all__ = [
    "RelationRow",
    "has_smime_keys_table",
    "index_add",
    "index_apply_renames",
    "index_remove",
    "reindex",
    "related_certificate_paths",
    "relation_add",
    "relation_exists",
    "relations_delete",
    "relations_for",
    "reset_customer_preferences",
]
