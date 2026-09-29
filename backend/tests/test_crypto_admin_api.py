"""Admin key management API (api/v1/admin/crypto_keys.py) against MariaDB + real gpg/openssl.

Direct router-call pattern (see test_admin_channels.py). Covers the Znuny
index side: ``smime_keys`` rows as SMIME.pm writes them, signer relations in
``smime_signer_cert_relations``, audit rows, the sign-key picker and queue
``default_sign_key`` validation. Everything the module commits is deleted at
the end (``TIQORA_STRICT_DB_LEAKS``).
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

pytest.importorskip("gnupg")

from tests._smime_fixtures import make_ca, make_leaf
from tiqora.api.v1.admin import crypto_keys as api
from tiqora.api.v1.admin import queues as queues_api
from tiqora.api.v1.admin.schemas import QueueUpdate
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.auth import AuthenticatedUser

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(
        shutil.which("gpg") is None or shutil.which("openssl") is None,
        reason="gpg/openssl not on PATH",
    ),
]

_CLEANUP_TABLES = (
    "smime_signer_cert_relations",
    "smime_keys",
    "tiqora_crypto_key",
    "tiqora_cache_invalidation",  # written by the queue update
)


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


def _admin() -> AuthenticatedUser:
    return AuthenticatedUser(
        id=1, login="root@localhost", first_name="Admin", last_name="Znuny", auth_method="session"
    )


@pytest.fixture
def dirs() -> Iterator[dict[str, str]]:
    with (
        tempfile.TemporaryDirectory(dir="/tmp") as gnupghome,  # noqa: S108 — gpg socket path
        tempfile.TemporaryDirectory() as base,
    ):
        yield {
            "gnupghome": gnupghome,
            "certs": str(Path(base) / "certs"),
            "private": str(Path(base) / "private"),
        }


@pytest.fixture
def settings(dirs: dict[str, str]) -> Settings:
    return Settings(
        TIQORA_CRYPTO_PGP_ENABLED="1",
        TIQORA_CRYPTO_PGP_GNUPGHOME=dirs["gnupghome"],
        TIQORA_CRYPTO_SMIME_ENABLED="1",
        TIQORA_CRYPTO_SMIME_CERT_DIR=dirs["certs"],
        TIQORA_CRYPTO_SMIME_PRIVATE_DIR=dirs["private"],
    )


@pytest_asyncio.fixture
async def session(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> AsyncIterator[AsyncSession]:
    sync = create_engine(mariadb_znuny_url)
    with sync.begin() as conn:
        # The audit table gained columns (alembic 0051): recreate it in shape.
        conn.execute(text("DROP TABLE IF EXISTS tiqora_crypto_key"))
        TiqoraBase.metadata.create_all(conn)
    # Queue validation resolves the config via get_settings().
    import tiqora.config as config_mod

    monkeypatch.setattr(config_mod, "get_settings", lambda: settings)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as s:
            yield s
    finally:
        await engine.dispose()
        with sync.begin() as conn:
            for table in _CLEANUP_TABLES:
                conn.execute(text(f"DELETE FROM {table}"))  # noqa: S608 — fixed names
            conn.execute(text("UPDATE queue SET default_sign_key = NULL WHERE id = 1"))
        sync.dispose()


async def _rows(session: AsyncSession, sql: str) -> list[tuple[object, ...]]:
    return [tuple(r) for r in (await session.execute(text(sql))).all()]


def _gen_pgp(gnupghome: str, email: str) -> str:
    import gnupg

    gpg = gnupg.GPG(gnupghome=gnupghome)
    key = gpg.gen_key(
        gpg.gen_key_input(
            name_email=email,
            key_type="RSA",
            key_length=2048,
            subkey_type="RSA",
            subkey_length=2048,
            no_protection=True,
        )
    )
    assert key.fingerprint
    return str(gpg.export_keys(str(key.fingerprint), secret=True, expect_passphrase=False))


async def test_status_reports_both_backends(session: AsyncSession, settings: Settings) -> None:
    out = await api.crypto_status(_admin(), session, settings)
    by = {s.backend: s for s in out}
    assert by["pgp"].enabled and by["pgp"].available, by["pgp"].problems
    assert by["smime"].enabled and by["smime"].available, by["smime"].problems
    assert by["smime"].binary.version.lower().startswith(("openssl", "libressl"))


async def test_pgp_upload_list_export_delete(
    session: AsyncSession, settings: Settings, dirs: dict[str, str]
) -> None:
    with tempfile.TemporaryDirectory(dir="/tmp") as scratch:  # noqa: S108
        armored = _gen_pgp(scratch, "queue@example.org")

    up = await api.upload_pgp_key(api.PgpUploadIn(ascii_armor=armored), _admin(), session, settings)
    assert len(up.fingerprints) == 1
    fp = up.fingerprints[0]
    assert up.keys[0].has_secret is True
    assert up.keys[0].emails == ["queue@example.org"]

    listed = await api.list_pgp_keys(_admin(), session, settings)
    assert [k.fingerprint for k in listed] == [fp]

    exported = await api.export_pgp_key(fp[-8:], _admin(), session, settings)
    assert b"BEGIN PGP PUBLIC KEY BLOCK" in exported.body
    assert "attachment" in exported.headers["content-disposition"]

    options = await api.sign_key_options(_admin(), session, settings, None, "queue@example.org")
    values = {o.value for o in options}
    key = listed[0].znuny_key_id
    assert {f"PGP::Detached::{key}", f"PGP::Inline::{key}"} <= values

    await api.delete_pgp_key(fp, _admin(), session, settings, secret=True)
    listed = await api.list_pgp_keys(_admin(), session, settings)
    assert listed[0].has_secret is False

    await api.delete_pgp_key(fp, _admin(), session, settings, secret=False)
    assert await api.list_pgp_keys(_admin(), session, settings) == []

    with pytest.raises(HTTPException) as exc:
        await api.delete_pgp_key(fp, _admin(), session, settings, secret=False)
    assert exc.value.status_code == 404

    actions = await _rows(
        session, "SELECT action, identifier, user_id FROM tiqora_crypto_key ORDER BY id"
    )
    assert actions == [("import", fp, 1), ("delete_secret", fp, 1), ("delete", fp, 1)]
    await session.execute(text("DELETE FROM tiqora_crypto_key"))
    await session.commit()


async def test_pgp_upload_garbage_is_422(session: AsyncSession, settings: Settings) -> None:
    with pytest.raises(HTTPException) as exc:
        await api.upload_pgp_key(
            api.PgpUploadIn(ascii_armor="-----BEGIN PGP PUBLIC KEY BLOCK----- nope"),
            _admin(),
            session,
            settings,
        )
    assert exc.value.status_code == 422


async def test_smime_lifecycle_keeps_znuny_index_in_sync(
    session: AsyncSession, settings: Settings, dirs: dict[str, str]
) -> None:
    ca = make_ca()
    leaf = make_leaf("agent@example.org", ca=ca)
    twin = make_leaf("agent@example.org", ca=ca)  # same DN → same hash, index .1

    ca_out = await api.upload_smime_certificate(
        api.SmimeCertificateIn(certificate=ca.cert_pem.decode()), _admin(), session, settings
    )
    leaf_out = await api.upload_smime_certificate(
        api.SmimeCertificateIn(certificate=leaf.cert_pem.decode()), _admin(), session, settings
    )
    twin_out = await api.upload_smime_certificate(
        api.SmimeCertificateIn(certificate=twin.cert_pem.decode()), _admin(), session, settings
    )
    h = leaf_out.hash
    assert (leaf_out.filename, twin_out.filename) == (f"{h}.0", f"{h}.1")
    assert ca_out.is_ca is True and leaf_out.status == "valid"

    # Duplicate upload → 422.
    with pytest.raises(HTTPException) as exc:
        await api.upload_smime_certificate(
            api.SmimeCertificateIn(certificate=leaf.cert_pem.decode()), _admin(), session, settings
        )
    assert exc.value.status_code == 422

    priv = await api.upload_smime_private_key(
        api.SmimePrivateKeyIn(private_key=twin.encrypted_key("geheim").decode(), secret="geheim"),
        _admin(),
        session,
        settings,
    )
    assert priv.certificate.filename == twin_out.filename
    assert priv.secret_generated is False
    assert (Path(dirs["private"]) / f"{twin_out.filename}.P").read_text() == "geheim"

    rows = await _rows(
        session,
        "SELECT key_type, file_name, key_hash, email_address, fingerprint"
        " FROM smime_keys ORDER BY key_type, file_name",
    )
    assert ("P", twin_out.filename, h, "agent@example.org", twin_out.fingerprint) in rows
    assert ("cert", leaf_out.filename, h, "agent@example.org", leaf_out.fingerprint) in rows
    assert len(rows) == 4

    # Signer relation: twin (has private key) ← CA.
    rel = await api.add_smime_relation(
        twin_out.filename,
        api.SmimeRelationIn(ca_filename=ca_out.filename),
        _admin(),
        session,
        settings,
    )
    assert [(r.ca_filename, r.ca_fingerprint) for r in rel] == [
        (ca_out.filename, ca_out.fingerprint)
    ]
    with pytest.raises(HTTPException) as exc:
        await api.add_smime_relation(
            twin_out.filename,
            api.SmimeRelationIn(ca_filename=ca_out.filename),
            _admin(),
            session,
            settings,
        )
    assert exc.value.status_code == 422  # relation exists
    with pytest.raises(HTTPException):
        await api.add_smime_relation(  # leaf has no private key
            leaf_out.filename,
            api.SmimeRelationIn(ca_filename=ca_out.filename),
            _admin(),
            session,
            settings,
        )

    dl = await api.download_smime_certificate(twin_out.filename, _admin(), session, settings)
    assert dl.body == twin.cert_pem

    options = await api.sign_key_options(_admin(), session, settings, None, "agent@example.org")
    assert [o.value for o in options] == [f"SMIME::Detached::{twin_out.filename}"]

    # Delete the .0 certificate: the twin moves down to .0 in files AND index.
    deleted = await api.delete_smime(leaf_out.filename, _admin(), session, settings, False)
    assert deleted.renamed == {f"{h}.1": f"{h}.0"}
    rows = await _rows(
        session, "SELECT key_type, file_name, fingerprint FROM smime_keys ORDER BY key_type"
    )
    assert sorted(rows) == sorted(
        [
            ("cert", ca_out.filename, ca_out.fingerprint),
            ("cert", f"{h}.0", twin_out.fingerprint),
            ("P", f"{h}.0", twin_out.fingerprint),
        ]
    )
    assert (Path(dirs["private"]) / f"{h}.0.P").read_text() == "geheim"
    rel = await api.list_smime_relations(f"{h}.0", _admin(), session, settings)
    assert len(rel) == 1  # relations follow fingerprints, not file names

    await api.delete_smime_relation(
        f"{h}.0", _admin(), session, settings, ca_fingerprint=ca_out.fingerprint
    )
    assert await api.list_smime_relations(f"{h}.0", _admin(), session, settings) == []

    # Private only, then the CA with its relations.
    await api.delete_smime(f"{h}.0", _admin(), session, settings, True)
    listed = await api.list_smime(_admin(), session, settings)
    assert {(c.filename, c.has_private) for c in listed} == {
        (ca_out.filename, False),
        (f"{h}.0", False),
    }
    with pytest.raises(HTTPException) as exc:
        await api.download_smime_certificate("../../etc.0", _admin(), session, settings)
    assert exc.value.status_code == 422
    with pytest.raises(HTTPException) as exc:
        await api.delete_smime("abcdef01.0", _admin(), session, settings, False)
    assert exc.value.status_code == 404

    actions = [r[0] for r in await _rows(session, "SELECT action FROM tiqora_crypto_key")]
    assert actions.count("add_certificate") == 3
    assert {"add_private", "relation_add", "relation_delete", "delete", "delete_private"} <= set(
        actions
    )
    for table in _CLEANUP_TABLES:
        await session.execute(text(f"DELETE FROM {table}"))  # noqa: S608
    await session.commit()


async def test_der_upload_and_unencrypted_key(session: AsyncSession, settings: Settings) -> None:
    import base64

    from cryptography.hazmat.primitives import serialization

    leaf = make_leaf("der@example.org")
    der_b64 = base64.b64encode(leaf.cert.public_bytes(serialization.Encoding.DER)).decode()
    cert = await api.upload_smime_certificate(
        api.SmimeCertificateIn(certificate=der_b64), _admin(), session, settings
    )
    priv = await api.upload_smime_private_key(
        api.SmimePrivateKeyIn(private_key=leaf.key_pem.decode()), _admin(), session, settings
    )
    assert priv.secret_generated is True
    assert priv.certificate.filename == cert.filename
    await api.delete_smime(cert.filename, _admin(), session, settings, False)
    for table in _CLEANUP_TABLES:
        await session.execute(text(f"DELETE FROM {table}"))  # noqa: S608
    await session.commit()


async def test_queue_default_sign_key_is_validated(
    session: AsyncSession, settings: Settings
) -> None:
    leaf = make_leaf("znuny@localhost")
    cert = await api.upload_smime_certificate(
        api.SmimeCertificateIn(certificate=leaf.cert_pem.decode()), _admin(), session, settings
    )
    await api.upload_smime_private_key(
        api.SmimePrivateKeyIn(private_key=leaf.key_pem.decode()), _admin(), session, settings
    )
    # Queue 1 (Postmaster) uses system address znuny@localhost.
    options = await api.sign_key_options(_admin(), session, settings, 1, None)
    value = f"SMIME::Detached::{cert.filename}"
    assert [o.value for o in options] == [value]

    with pytest.raises(HTTPException) as exc:
        await queues_api.update_queue(1, QueueUpdate(default_sign_key="garbage"), _admin(), session)
    assert exc.value.status_code == 422
    with pytest.raises(HTTPException) as exc:
        await queues_api.update_queue(
            1, QueueUpdate(default_sign_key="SMIME::Detached::abcdef01.0"), _admin(), session
        )
    assert exc.value.status_code == 422
    await session.rollback()

    queue = await queues_api.update_queue(1, QueueUpdate(default_sign_key=value), _admin(), session)
    assert queue.default_sign_key == value
    queue = await queues_api.update_queue(1, QueueUpdate(default_sign_key=""), _admin(), session)
    assert queue.default_sign_key is None

    await api.delete_smime(cert.filename, _admin(), session, settings, False)
    for table in _CLEANUP_TABLES:
        await session.execute(text(f"DELETE FROM {table}"))  # noqa: S608
    await session.commit()


async def test_legacy_smime_register_alias(session: AsyncSession, settings: Settings) -> None:
    leaf = make_leaf("alias@example.org")
    out = await api.admin_smime_register(
        api.SmimeRegisterIn(
            email="alias@example.org",
            cert_pem=leaf.cert_pem.decode(),
            key_pem=leaf.key_pem.decode(),
        ),
        _admin(),
        session,
        settings,
    )
    assert out.has_cert and out.has_key and out.filename
    audit = await api.list_crypto_keys(_admin(), session)
    assert {a.action for a in audit} == {"add_certificate", "add_private"}
    await api.delete_smime(out.filename, _admin(), session, settings, False)
    for table in _CLEANUP_TABLES:
        await session.execute(text(f"DELETE FROM {table}"))  # noqa: S608
    await session.commit()


async def test_migrate_flat_store_moves_files_into_znuny_layout(
    session: AsyncSession, settings: Settings, tmp_path: Path
) -> None:
    from tiqora.crypto.keystore import migrate_flat_store
    from tiqora.crypto.smime_store import SmimeStore

    flat_certs = tmp_path / "flat-certs"
    flat_keys = tmp_path / "flat-keys"
    flat_certs.mkdir()
    flat_keys.mkdir()
    with_key = make_leaf("old@example.org")
    cert_only = make_leaf("peer@example.org")
    (flat_certs / "old@example.org.crt").write_bytes(with_key.cert_pem)
    (flat_keys / "old@example.org.key").write_bytes(with_key.key_pem)
    (flat_certs / "peer@example.org.crt").write_bytes(cert_only.cert_pem)

    store = SmimeStore(settings.crypto_smime_cert_dir, settings.crypto_smime_private_dir)
    dry = await migrate_flat_store(session, store, str(flat_certs), str(flat_keys), dry_run=True)
    assert len(dry.migrated) == 2 and store.list_entries() == []

    report = await migrate_flat_store(session, store, str(flat_certs), str(flat_keys))
    assert report.errors == []
    assert len(report.removed_files) == 3
    assert list(flat_certs.iterdir()) == [] and list(flat_keys.iterdir()) == []
    entries = {e.info.emails[0]: e for e in store.list_entries() if e.info}
    assert entries["old@example.org"].has_private is True
    assert entries["peer@example.org"].has_private is False
    rows = await _rows(session, "SELECT key_type FROM smime_keys")
    assert sorted(str(r[0]) for r in rows) == ["P", "cert", "cert"]

    # Idempotent: a second run over re-created flat files skips duplicates.
    (flat_certs / "peer@example.org.crt").write_bytes(cert_only.cert_pem)
    again = await migrate_flat_store(session, store, str(flat_certs), str(flat_keys), keep=True)
    assert again.errors == [] and len(again.skipped) == 1

    for e in store.list_entries():
        store.remove_certificate(e.filename)
    for table in _CLEANUP_TABLES:
        await session.execute(text(f"DELETE FROM {table}"))  # noqa: S608
    await session.commit()
