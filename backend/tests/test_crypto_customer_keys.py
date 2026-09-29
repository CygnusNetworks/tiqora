"""Customer keys (Znuny Preferences::PGP / ::SMIME) and SMIME::FetchFromCustomer.

MariaDB + real gpg/openssl. Covers the customer_preferences Znuny writes
(PGPKeyID/PGPFilename, SMIMEHash/SMIMEFingerprint/SMIMEFilename), the upload
guards, delete, the postmaster pre-filter and the daily renew. Everything
committed is removed at the end (``TIQORA_STRICT_DB_LEAKS``).
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import pkcs7
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

pytest.importorskip("gnupg")

from tests._smime_fixtures import make_ca, make_leaf
from tiqora.config import Settings
from tiqora.crypto import CryptoNotFoundError
from tiqora.crypto import customer_fetch as cf
from tiqora.crypto import customer_keys as ck
from tiqora.crypto.config import CryptoConfig
from tiqora.crypto.pgp import PgpEngine
from tiqora.crypto.smime_store import SmimeStore
from tiqora.db.tiqora.base import TiqoraBase

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(
        shutil.which("gpg") is None or shutil.which("openssl") is None,
        reason="gpg/openssl not on PATH",
    ),
]

LOGIN = "ckeys-carla"
EMAIL = "carla.ckeys@example.com"
CERT_COLUMN = "tiqora_test_smime_cert"


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


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
        TIQORA_CUSTOMER_SMIME_CERT_COLUMN=CERT_COLUMN,
    )


@pytest.fixture
def config(settings: Settings) -> CryptoConfig:
    base = CryptoConfig.from_settings(settings)
    from dataclasses import replace

    return CryptoConfig(pgp=base.pgp, smime=replace(base.smime, fetch_from_customer=True))


@pytest_asyncio.fixture
async def session(mariadb_znuny_url: str) -> AsyncIterator[AsyncSession]:
    sync = create_engine(mariadb_znuny_url)
    with sync.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS tiqora_crypto_key"))
        TiqoraBase.metadata.create_all(conn)
        conn.execute(text("DELETE FROM customer_user WHERE login LIKE 'ckeys-%'"))
        conn.execute(
            text(
                "INSERT INTO customer_user (login, email, customer_id, first_name, last_name,"
                " pw, valid_id, create_time, create_by, change_time, change_by)"
                " VALUES (:l, :e, 'ckeys', 'Carla', 'Customer', 'x', 1,"
                " current_timestamp, 1, current_timestamp, 1)"
            ),
            {"l": LOGIN, "e": EMAIL},
        )
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as s:
            yield s
    finally:
        await engine.dispose()
        with sync.begin() as conn:
            for table in ("smime_keys", "tiqora_crypto_key"):
                conn.execute(text(f"DELETE FROM {table}"))  # noqa: S608 — fixed names
            conn.execute(text("DELETE FROM customer_preferences WHERE user_id LIKE 'ckeys-%'"))
            conn.execute(text("DELETE FROM customer_user WHERE login LIKE 'ckeys-%'"))
            cols = {
                r[0]
                for r in conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns"
                        " WHERE table_schema = DATABASE() AND table_name = 'customer_user'"
                    )
                )
            }
            if CERT_COLUMN in cols:
                conn.execute(text(f"ALTER TABLE customer_user DROP COLUMN {CERT_COLUMN}"))
        sync.dispose()


def _gen_pgp(email: str, *, secret: bool = False) -> str:
    import gnupg

    with tempfile.TemporaryDirectory(dir="/tmp") as scratch:  # noqa: S108
        gpg = gnupg.GPG(gnupghome=scratch)
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
        return str(gpg.export_keys(str(key.fingerprint), secret=secret, expect_passphrase=False))


async def _prefs(session: AsyncSession) -> dict[str, str]:
    return await ck.get_preferences(session, LOGIN)


# ------------------------------------------------------------------- PGP


async def test_pgp_upload_sets_znuny_preferences_and_lists(
    session: AsyncSession, config: CryptoConfig
) -> None:
    engine = PgpEngine.from_config(config.pgp)
    key = await ck.add_customer_pgp_key(
        session,
        engine,
        login=LOGIN,
        email=EMAIL,
        armored=_gen_pgp(EMAIL),
        user_id=None,
        require_own_email=True,
    )
    prefs = await _prefs(session)
    assert prefs["PGPKeyID"] == key.key_id
    assert prefs["PGPFilename"] == f"{key.uids[0]}-2048-{key.short_id}.pub"
    assert [k.fingerprint for k in ck.customer_pgp_keys(engine, EMAIL, prefs)] == [key.fingerprint]
    audit = (
        await session.execute(text("SELECT action, user_id, detail FROM tiqora_crypto_key"))
    ).all()
    assert [tuple(r) for r in audit] == [("import", None, f"customer {LOGIN}")]


async def test_pgp_upload_guards(session: AsyncSession, config: CryptoConfig) -> None:
    engine = PgpEngine.from_config(config.pgp)
    with pytest.raises(ck.CustomerKeyError, match="public"):
        await ck.add_customer_pgp_key(
            session,
            engine,
            login=LOGIN,
            email=EMAIL,
            armored=_gen_pgp(EMAIL, secret=True),
            user_id=None,
            require_own_email=False,
        )
    # Another address that merely contains the customer's one is foreign, too.
    other = _gen_pgp("x" + EMAIL)
    with pytest.raises(ck.CustomerKeyError, match="email"):
        await ck.add_customer_pgp_key(
            session,
            engine,
            login=LOGIN,
            email=EMAIL,
            armored=other,
            user_id=None,
            require_own_email=True,
        )
    assert engine.list_keys() == []
    # An agent may link a key for another address of the customer.
    key = await ck.add_customer_pgp_key(
        session, engine, login=LOGIN, email=EMAIL, armored=other, user_id=1, require_own_email=False
    )
    prefs = await _prefs(session)
    assert [k.fingerprint for k in ck.customer_pgp_keys(engine, EMAIL, prefs)] == [key.fingerprint]


async def test_pgp_delete_clears_preferences(session: AsyncSession, config: CryptoConfig) -> None:
    engine = PgpEngine.from_config(config.pgp)
    key = await ck.add_customer_pgp_key(
        session,
        engine,
        login=LOGIN,
        email=EMAIL,
        armored=_gen_pgp(EMAIL),
        user_id=1,
        require_own_email=True,
    )
    with pytest.raises(CryptoNotFoundError):
        await ck.delete_customer_pgp_key(
            session, engine, login=LOGIN, email="other@example.com", key_ref="DEADBEEF", user_id=1
        )
    await ck.delete_customer_pgp_key(
        session, engine, login=LOGIN, email=EMAIL, key_ref=key.fingerprint, user_id=1
    )
    assert engine.list_keys() == []
    prefs = await _prefs(session)
    assert prefs["PGPKeyID"] == "" and prefs["PGPFilename"] == ""


async def test_pgp_delete_refuses_secret_keys(session: AsyncSession, config: CryptoConfig) -> None:
    engine = PgpEngine.from_config(config.pgp)
    fps = engine.import_key(_gen_pgp(EMAIL, secret=True))
    with pytest.raises(ck.CustomerKeyError, match="admin"):
        await ck.delete_customer_pgp_key(
            session, engine, login=LOGIN, email=EMAIL, key_ref=fps[0], user_id=1
        )


# ---------------------------------------------------------------- S/MIME


async def test_smime_upload_sets_znuny_preferences(
    session: AsyncSession, config: CryptoConfig
) -> None:
    store = SmimeStore.from_config(config.smime)
    leaf = make_leaf(EMAIL, ca=make_ca())
    der = leaf.cert.public_bytes(serialization.Encoding.DER)
    entry = await ck.add_customer_smime_certificate(
        session, store, login=LOGIN, email=EMAIL, data=der, user_id=None, require_own_email=True
    )
    assert entry.info is not None
    prefs = await _prefs(session)
    assert prefs["SMIMEFilename"] == entry.filename
    assert prefs["SMIMEHash"] == entry.info.hash
    assert prefs["SMIMEFingerprint"] == entry.info.fingerprint
    rows = (await session.execute(text("SELECT file_name, key_type FROM smime_keys"))).all()
    assert [tuple(r) for r in rows] == [(entry.filename, "cert")]
    # Re-upload links the stored certificate instead of failing.
    again = await ck.add_customer_smime_certificate(
        session,
        store,
        login=LOGIN,
        email=EMAIL,
        data=leaf.cert_pem,
        user_id=None,
        require_own_email=True,
    )
    assert again.filename == entry.filename
    assert len(store.list_entries()) == 1
    listed = ck.customer_smime_certs(store, EMAIL, prefs)
    assert [e.filename for e in listed] == [entry.filename]


async def test_smime_upload_guards(session: AsyncSession, config: CryptoConfig) -> None:
    store = SmimeStore.from_config(config.smime)
    ca = make_ca()
    with pytest.raises(ck.CustomerKeyError, match="CA"):
        await ck.add_customer_smime_certificate(
            session,
            store,
            login=LOGIN,
            email=EMAIL,
            data=ca.cert_pem,
            user_id=1,
            require_own_email=False,
        )
    with pytest.raises(ck.CustomerKeyError, match="email"):
        await ck.add_customer_smime_certificate(
            session,
            store,
            login=LOGIN,
            email=EMAIL,
            data=make_leaf("mallory@example.com", ca=ca).cert_pem,
            user_id=None,
            require_own_email=True,
        )
    assert store.list_entries() == []


async def test_smime_delete_clears_preferences(session: AsyncSession, config: CryptoConfig) -> None:
    store = SmimeStore.from_config(config.smime)
    entry = await ck.add_customer_smime_certificate(
        session,
        store,
        login=LOGIN,
        email=EMAIL,
        data=make_leaf(EMAIL).cert_pem,
        user_id=1,
        require_own_email=True,
    )
    await ck.delete_customer_smime_certificate(
        session, store, login=LOGIN, email=EMAIL, filename=entry.filename, user_id=1
    )
    assert store.list_entries() == []
    prefs = await _prefs(session)
    assert prefs["SMIMEFilename"] == "" and prefs["SMIMEFingerprint"] == ""


# ------------------------------------------------------- FetchFromCustomer


async def _add_cert_column(session: AsyncSession, value: bytes | None) -> None:
    await session.execute(text(f"ALTER TABLE customer_user ADD COLUMN {CERT_COLUMN} TEXT NULL"))
    await session.execute(
        text(f"UPDATE customer_user SET {CERT_COLUMN} = :v WHERE login = :l"),  # noqa: S608
        {"v": value.decode() if value else None, "l": LOGIN},
    )
    await session.commit()


async def test_fetch_from_customer_column_pkcs7(
    session: AsyncSession, settings: Settings, config: CryptoConfig
) -> None:
    leaf = make_leaf(EMAIL, ca=make_ca())
    p7 = pkcs7.serialize_certificates([leaf.cert], serialization.Encoding.PEM)
    await _add_cert_column(session, p7)

    report = await cf.fetch_for_sender(session, settings, config, f"Carla <{EMAIL.upper()}>")
    assert len(report.added) == 1, report.errors
    store = SmimeStore.from_config(config.smime)
    assert [e.filename for e in store.search(EMAIL)] == report.added
    # Second mail: already stored, nothing added.
    again = await cf.fetch_for_sender(session, settings, config, EMAIL)
    assert again.added == [] and again.errors == []


async def test_fetch_is_off_unless_enabled_and_customer(
    session: AsyncSession, settings: Settings, config: CryptoConfig
) -> None:
    from dataclasses import replace

    await _add_cert_column(session, make_leaf(EMAIL).cert_pem)
    off = CryptoConfig(pgp=config.pgp, smime=replace(config.smime, fetch_from_customer=False))
    assert (await cf.fetch_for_sender(session, settings, off, EMAIL)).added == []
    # Unknown address: no customer user, no lookup.
    assert (await cf.fetch_for_sender(session, settings, config, "x@example.org")).added == []
    # Pre-filter module deactivated in Znuny's SysConfig (modified row, is_valid 0).
    params = {"n": cf.PREFILTER_SETTING, "x": "x", "e": "---\nModule: x\n"}
    await session.execute(
        text(
            "INSERT INTO sysconfig_default (name, description, navigation, is_invisible,"
            " is_readonly, is_required, is_valid, has_configlevel, user_modification_possible,"
            " user_modification_active, xml_content_raw, xml_content_parsed, xml_filename,"
            " effective_value, is_dirty, exclusive_lock_guid, create_time, create_by,"
            " change_time, change_by) VALUES (:n, :x, :x, 0, 0, 0, 1, 0, 0, 0, :x, :x,"
            " 'Ticket.xml', :e, 0, '0', current_timestamp, 1, current_timestamp, 1)"
        ),
        params,
    )
    await session.execute(
        text(
            "INSERT INTO sysconfig_modified (sysconfig_default_id, name, user_id, is_valid,"
            " user_modification_active, effective_value, is_dirty, reset_to_default,"
            " create_time, create_by, change_time, change_by)"
            " SELECT id, name, NULL, 0, 0, effective_value, 0, 0,"
            " current_timestamp, 1, current_timestamp, 1 FROM sysconfig_default WHERE name = :n"
        ),
        params,
    )
    await session.commit()
    try:
        assert (await cf.fetch_for_sender(session, settings, config, EMAIL)).added == []
    finally:
        await session.execute(text("DELETE FROM sysconfig_modified WHERE name = :n"), params)
        await session.execute(text("DELETE FROM sysconfig_default WHERE name = :n"), params)
        await session.commit()
    assert len((await cf.fetch_for_sender(session, settings, config, EMAIL)).added) == 1


async def test_renew_adds_the_new_backend_certificate(
    session: AsyncSession, settings: Settings, config: CryptoConfig
) -> None:
    store = SmimeStore.from_config(config.smime)
    ca = make_ca()
    old = make_leaf(EMAIL, ca=ca)
    store.add_certificate(old.cert_pem)  # the certificate the customer had so far
    new = make_leaf(EMAIL, ca=ca)
    await _add_cert_column(session, new.cert_pem)

    report = await cf.renew_customer_certificates(session, settings, config)
    assert len(report.added) == 1
    assert len(store.search(EMAIL)) == 2  # old one stays, as in Znuny
    assert (await cf.renew_customer_certificates(session, settings, config)).added == []


async def test_fetch_from_customer_ldap(
    session: AsyncSession, settings: Settings, config: CryptoConfig, monkeypatch: Any
) -> None:
    import ldap3

    leaf = make_leaf(EMAIL, ca=make_ca())
    der_p7 = pkcs7.serialize_certificates([leaf.cert], serialization.Encoding.DER)
    server = ldap3.Server("fake")
    conn = ldap3.Connection(
        server, user="cn=admin,dc=example,dc=com", password="x", client_strategy=ldap3.MOCK_SYNC
    )
    conn.strategy.add_entry(
        "cn=carla,ou=people,dc=example,dc=com",
        {"objectClass": ["inetOrgPerson"], "mail": [EMAIL], "userSMIMECertificate": [der_p7]},
    )
    conn.bind()
    monkeypatch.setattr(cf, "_ldap_connection", lambda _s: conn)
    ldap_settings = settings.model_copy(
        update={
            "customer_ldap_enabled": True,
            "customer_ldap_host": "fake",
            "customer_ldap_base_dn": "ou=people,dc=example,dc=com",
            "customer_smime_cert_column": "",
        }
    )
    report = await cf.fetch_from_customer(session, ldap_settings, config, EMAIL)
    assert len(report.added) == 1, report.errors


async def test_renew_worker_tick(
    mariadb_znuny_url: str,
    session: AsyncSession,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace

    from tiqora.worker import smime_customer_renew as job

    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(job, "get_session_factory", lambda: factory)
    monkeypatch.setattr(job, "get_settings", lambda: settings)
    try:
        # SMIME on, SMIME::FetchFromCustomer off (Znuny default) → no-op.
        assert await job.run_smime_customer_renew_tick() == {
            "enabled": 1,
            "fetch_from_customer": 0,
        }

        async def _cfg(_s: Settings, _sc: Any) -> CryptoConfig:
            base = CryptoConfig.from_settings(settings)
            return CryptoConfig(pgp=base.pgp, smime=replace(base.smime, fetch_from_customer=True))

        monkeypatch.setattr(job, "resolve_crypto_config", _cfg)
        store = SmimeStore.from_config(CryptoConfig.from_settings(settings).smime)
        store.add_certificate(make_leaf(EMAIL).cert_pem)
        await _add_cert_column(session, make_leaf(EMAIL).cert_pem)
        out = await job.run_smime_customer_renew_tick()
        assert out["added"] == 1 and out["errors"] == 0
    finally:
        await engine.dispose()
