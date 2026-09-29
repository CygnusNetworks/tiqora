"""Agent customer-key API (api/v1/customer_keys.py) + portal preferences API.

MariaDB + real gpg/openssl, direct router calls. Agents: read for everyone,
write for rw in ``admin``/``users`` (Znuny AdminCustomerUser). Portal: the
customer's own language, password and keys. Cleans up everything it commits.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

pytest.importorskip("gnupg")

from tests._smime_fixtures import make_ca, make_leaf
from tiqora.api.v1 import customer_keys as api
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

LOGIN = "ckapi-carla"
EMAIL = "carla.ckapi@example.com"
PLAIN_ID = 471_113


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


def _agent(user_id: int = 1) -> AuthenticatedUser:
    return AuthenticatedUser(
        id=user_id, login=f"u{user_id}", first_name="A", last_name="B", auth_method="session"
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


def _settings(dirs: dict[str, str], *, pgp: bool = True, smime: bool = True) -> Settings:
    return Settings(
        TIQORA_CRYPTO_PGP_ENABLED="1" if pgp else "0",
        TIQORA_CRYPTO_PGP_GNUPGHOME=dirs["gnupghome"],
        TIQORA_CRYPTO_SMIME_ENABLED="1" if smime else "0",
        TIQORA_CRYPTO_SMIME_CERT_DIR=dirs["certs"],
        TIQORA_CRYPTO_SMIME_PRIVATE_DIR=dirs["private"],
    )


@pytest.fixture
def settings(dirs: dict[str, str]) -> Settings:
    return _settings(dirs)


@pytest_asyncio.fixture
async def session(mariadb_znuny_url: str) -> AsyncIterator[AsyncSession]:
    sync = create_engine(mariadb_znuny_url)
    with sync.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS tiqora_crypto_key"))
        TiqoraBase.metadata.create_all(conn)
        conn.execute(text("DELETE FROM customer_user WHERE login LIKE 'ckapi-%'"))
        conn.execute(
            text(
                "INSERT INTO customer_user (login, email, customer_id, first_name, last_name,"
                " pw, valid_id, create_time, create_by, change_time, change_by)"
                " VALUES (:l, :e, 'ckapi', 'Carla', 'Customer', :pw, 1,"
                " current_timestamp, 1, current_timestamp, 1)"
            ),
            {"l": LOGIN, "e": EMAIL, "pw": _hash("old password 123")},
        )
        conn.execute(text("DELETE FROM users WHERE id = :i"), {"i": PLAIN_ID})
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:i, 'ckapi.plain', 'x', 'Plain', 'Agent', 1,"
                " current_timestamp, 1, current_timestamp, 1)"
            ),
            {"i": PLAIN_ID},
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
            conn.execute(text("DELETE FROM customer_preferences WHERE user_id LIKE 'ckapi-%'"))
            conn.execute(text("DELETE FROM customer_user WHERE login LIKE 'ckapi-%'"))
            conn.execute(text("DELETE FROM users WHERE id = :i"), {"i": PLAIN_ID})
        sync.dispose()


def _hash(pw: str) -> str:
    from tiqora.znuny.password import hash_password

    return hash_password(pw)


def _gen_pgp(email: str) -> str:
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
        return str(gpg.export_keys(str(key.fingerprint)))


# ------------------------------------------------------------------ agent


async def test_agent_upload_list_delete(session: AsyncSession, settings: Settings) -> None:
    out = await api.get_customer_crypto_keys(LOGIN, _agent(), session, settings)
    assert out.pgp_enabled and out.smime_enabled and out.can_edit
    assert out.pgp_keys == [] and out.smime_certificates == []

    out = await api.upload_customer_pgp_key(
        LOGIN, api.CustomerPgpKeyIn(ascii_armor=_gen_pgp(EMAIL)), _agent(), session, settings
    )
    assert [k.emails for k in out.pgp_keys] == [[EMAIL]]
    assert out.pgp_key_id == out.pgp_keys[0].key_id

    cert = make_leaf(EMAIL, ca=make_ca()).cert_pem.decode()
    out = await api.upload_customer_smime_certificate(
        LOGIN, api.CustomerSmimeCertificateIn(certificate=cert), _agent(), session, settings
    )
    assert len(out.smime_certificates) == 1
    filename = out.smime_certificates[0].filename
    assert out.smime_filename == filename

    out = await api.delete_customer_smime_certificate(LOGIN, filename, _agent(), session, settings)
    assert out.smime_certificates == [] and out.smime_filename is None
    fp = out.pgp_keys[0].fingerprint
    out = await api.delete_customer_pgp_key(LOGIN, fp, _agent(), session, settings)
    assert out.pgp_keys == [] and out.pgp_key_id is None


async def test_agent_without_rw_can_read_not_write(
    session: AsyncSession, settings: Settings
) -> None:
    out = await api.get_customer_crypto_keys(LOGIN, _agent(PLAIN_ID), session, settings)
    assert out.can_edit is False
    with pytest.raises(HTTPException) as exc:
        await api.upload_customer_pgp_key(
            LOGIN,
            api.CustomerPgpKeyIn(ascii_armor=_gen_pgp(EMAIL)),
            _agent(PLAIN_ID),
            session,
            settings,
        )
    assert exc.value.status_code == 403


async def test_agent_errors(session: AsyncSession, dirs: dict[str, str]) -> None:
    off = _settings(dirs, pgp=False)
    with pytest.raises(HTTPException) as exc:
        await api.get_customer_crypto_keys("ckapi-nobody", _agent(), session, off)
    assert exc.value.status_code == 404
    out = await api.get_customer_crypto_keys(LOGIN, _agent(), session, off)
    assert out.pgp_enabled is False
    with pytest.raises(HTTPException) as exc:
        await api.upload_customer_pgp_key(
            LOGIN, api.CustomerPgpKeyIn(ascii_armor=_gen_pgp(EMAIL)), _agent(), session, off
        )
    assert exc.value.status_code == 409
    with pytest.raises(HTTPException) as exc:
        await api.upload_customer_smime_certificate(
            LOGIN,
            api.CustomerSmimeCertificateIn(certificate=make_ca().cert_pem.decode()),
            _agent(),
            session,
            off,
        )
    assert exc.value.status_code == 422
    assert "CA" in str(exc.value.detail)


# ------------------------------------------------------------------ portal


def _customer() -> Any:
    from tiqora.domain.customer_auth import AuthenticatedCustomer

    return AuthenticatedCustomer(
        id=0,
        login=LOGIN,
        email=EMAIL,
        customer_id="ckapi",
        first_name="Carla",
        last_name="Customer",
    )


async def _customer_id(session: AsyncSession) -> int:
    return int(
        (
            await session.execute(
                text("SELECT id FROM customer_user WHERE login = :l"), {"l": LOGIN}
            )
        ).scalar_one()
    )


class _Limiter:
    def __init__(self) -> None:
        self.failures = 0
        self.resets = 0

    async def check(self, *, login: str, ip: str) -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(allowed=self.failures < 3, retry_after=0)

    async def record_failure(self, *, login: str, ip: str) -> None:
        assert login.startswith("portal-password:")
        self.failures += 1

    async def reset(self, *, login: str, ip: str | None = None) -> None:
        self.resets += 1


def _request() -> Any:
    from starlette.requests import Request

    return Request({"type": "http", "headers": [], "client": ("127.0.0.1", 1)})


async def test_portal_preferences_language_and_keys(
    session: AsyncSession, settings: Settings
) -> None:
    from tiqora.api.portal import preferences as portal

    me = _customer()
    out = await portal.get_preferences(me, session, settings)
    assert out.language is None
    assert out.language_enabled and out.password_enabled
    assert out.pgp_enabled and out.smime_enabled

    out = await portal.set_language(portal.PortalLanguageIn(language="de"), me, session, settings)
    assert out.language == "de"
    with pytest.raises(HTTPException) as exc:
        await portal.set_language(portal.PortalLanguageIn(language="../x"), me, session, settings)
    assert exc.value.status_code == 422

    out = await portal.upload_pgp_key(
        api.CustomerPgpKeyIn(ascii_armor=_gen_pgp(EMAIL)), me, session, settings
    )
    assert [k.emails for k in out.keys.pgp_keys] == [[EMAIL]]
    # Someone else's address is refused for self-service uploads.
    with pytest.raises(HTTPException) as exc:
        await portal.upload_smime_certificate(
            api.CustomerSmimeCertificateIn(
                certificate=make_leaf("boss@example.com").cert_pem.decode()
            ),
            me,
            session,
            settings,
        )
    assert exc.value.status_code == 422
    out = await portal.upload_smime_certificate(
        api.CustomerSmimeCertificateIn(certificate=make_leaf(EMAIL).cert_pem.decode()),
        me,
        session,
        settings,
    )
    assert len(out.keys.smime_certificates) == 1
    prefs = (
        await session.execute(
            text(
                "SELECT preferences_key FROM customer_preferences WHERE user_id = :l"
                " ORDER BY preferences_key"
            ),
            {"l": LOGIN},
        )
    ).scalars()
    assert list(prefs) == [
        "PGPFilename",
        "PGPKeyID",
        "SMIMEFilename",
        "SMIMEFingerprint",
        "SMIMEHash",
        "UserLanguage",
    ]


async def test_portal_keys_hidden_while_backend_off(
    session: AsyncSession, dirs: dict[str, str]
) -> None:
    from tiqora.api.portal import preferences as portal

    off = _settings(dirs, pgp=False, smime=False)
    out = await portal.get_preferences(_customer(), session, off)
    assert not out.pgp_enabled and not out.smime_enabled
    with pytest.raises(HTTPException) as exc:
        await portal.upload_pgp_key(
            api.CustomerPgpKeyIn(ascii_armor=_gen_pgp(EMAIL)), _customer(), session, off
        )
    assert exc.value.status_code == 404


async def test_portal_password_change(session: AsyncSession) -> None:
    from dataclasses import replace

    from tiqora.api.portal import preferences as portal
    from tiqora.znuny.password import verify_password

    me = replace(_customer(), id=await _customer_id(session))
    limiter = _Limiter()
    with pytest.raises(HTTPException) as exc:
        await portal.change_password(
            portal.PortalPasswordIn(current_password="wrong", new_password="a" * 20),
            _request(),
            me,
            session,
            limiter,  # type: ignore[arg-type]
        )
    assert exc.value.status_code == 422 and limiter.failures == 1
    with pytest.raises(HTTPException) as exc:
        await portal.change_password(
            portal.PortalPasswordIn(current_password="old password 123", new_password="short"),
            _request(),
            me,
            session,
            limiter,  # type: ignore[arg-type]
        )
    assert exc.value.status_code == 422 and "at least" in str(exc.value.detail)
    await portal.change_password(
        portal.PortalPasswordIn(
            current_password="old password 123", new_password="new password 456"
        ),
        _request(),
        me,
        session,
        limiter,  # type: ignore[arg-type]
    )
    stored = (
        await session.execute(text("SELECT pw FROM customer_user WHERE login = :l"), {"l": LOGIN})
    ).scalar_one()
    assert verify_password("new password 456", stored)
    assert limiter.resets == 1
