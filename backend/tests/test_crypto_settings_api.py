"""Admin crypto settings API (api/v1/admin/crypto_settings.py) against MariaDB + real gpg.

Direct router-call pattern (see test_crypto_admin_api.py). The keyring path
comes from ``TIQORA_CRYPTO_PGP_GNUPGHOME``, so that field is env-locked; the
rest is stored in ``tiqora_settings``. Everything committed is deleted again
(``TIQORA_STRICT_DB_LEAKS``).
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

pytest.importorskip("gnupg")

from tiqora.api.v1.admin import crypto_settings as api
from tiqora.config import Settings
from tiqora.crypto.config import load_crypto_config
from tiqora.crypto.pgp import PgpEngine
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.auth import AuthenticatedUser

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(shutil.which("gpg") is None, reason="gpg not on PATH"),
]

_PASSPHRASE = "correct horse battery"


def _admin() -> AuthenticatedUser:
    return AuthenticatedUser(
        id=1, login="root@localhost", first_name="Admin", last_name="Znuny", auth_method="session"
    )


@pytest.fixture
def gnupghome() -> Iterator[str]:
    with tempfile.TemporaryDirectory(dir="/tmp") as d:  # noqa: S108 — gpg socket path
        yield d


@pytest.fixture
def settings(gnupghome: str) -> Settings:
    return Settings(TIQORA_CRYPTO_PGP_GNUPGHOME=gnupghome)


@pytest.fixture
def secret_fp(gnupghome: str) -> str:
    import gnupg

    gpg = gnupg.GPG(gnupghome=gnupghome)
    key = gpg.gen_key(
        gpg.gen_key_input(
            name_real="Erika Beispiel",
            name_email="abuse@example.org",
            key_type="RSA",
            key_length=2048,
            subkey_type="RSA",
            subkey_length=2048,
            expire_date="0",
            passphrase=_PASSPHRASE,
        )
    )
    assert key.fingerprint, key.stderr
    return str(key.fingerprint)


@pytest_asyncio.fixture
async def session(mariadb_znuny_url: str) -> AsyncIterator[AsyncSession]:
    sync = create_engine(mariadb_znuny_url)
    with sync.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS tiqora_crypto_key"))
        TiqoraBase.metadata.create_all(conn)
    engine = create_async_engine(mariadb_znuny_url.replace("mysql+pymysql://", "mysql+aiomysql://"))
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as s:
            yield s
    finally:
        await engine.dispose()
        with sync.begin() as conn:
            conn.execute(text("DELETE FROM tiqora_settings WHERE `key` LIKE 'crypto.%'"))
            conn.execute(text("DELETE FROM tiqora_crypto_key"))
        sync.dispose()


def _field(out: api.CryptoSettingsOut, name: str) -> api.CryptoSettingOut:
    return next(f for f in [*out.pgp, *out.smime] if f.name == name)


async def test_get_lists_all_fields_with_sources(session: AsyncSession, settings: Settings) -> None:
    out = await api.get_crypto_settings(_admin(), session, settings)
    names = {f.name for f in [*out.pgp, *out.smime]}
    assert {"pgp.enabled", "pgp.method", "pgp.trusted_network", "smime.no_verify"} <= names
    home = _field(out, "pgp.homedir")
    assert home.source == "env" and home.locked and home.env_var == "TIQORA_CRYPTO_PGP_GNUPGHOME"
    assert _field(out, "pgp.method").choices == ["Detached", "Inline"]


async def test_put_stores_and_clears_tiqora_values(
    session: AsyncSession, settings: Settings
) -> None:
    out = await api.put_crypto_settings(
        api.CryptoSettingsUpdate(values={"pgp.enabled": True, "smime.ca_path": "/etc/ca.pem"}),
        _admin(),
        session,
        settings,
    )
    assert _field(out, "pgp.enabled").value is True
    assert _field(out, "pgp.enabled").source == "tiqora"
    cfg = await load_crypto_config(session, settings)
    assert cfg.pgp.enabled is True and cfg.smime.ca_path == "/etc/ca.pem"

    out = await api.put_crypto_settings(
        api.CryptoSettingsUpdate(values={"pgp.enabled": None}), _admin(), session, settings
    )
    assert _field(out, "pgp.enabled").source != "tiqora"


@pytest.mark.parametrize(
    ("values", "code"),
    [
        ({"pgp.homedir": "/elsewhere"}, 409),  # env-locked
        ({"pgp.method": "Sideways"}, 422),
        ({"nope": True}, 422),
    ],
)
async def test_put_rejects_locked_invalid_unknown(
    session: AsyncSession, settings: Settings, values: dict[str, object], code: int
) -> None:
    with pytest.raises(HTTPException) as exc:
        await api.put_crypto_settings(
            api.CryptoSettingsUpdate(values=values), _admin(), session, settings
        )
    assert exc.value.status_code == code


async def test_passphrase_is_checked_stored_encrypted_and_never_returned(
    session: AsyncSession, settings: Settings, secret_fp: str
) -> None:
    out = await api.get_crypto_settings(_admin(), session, settings)
    assert [p.source for p in out.pgp_passphrases if p.fingerprint == secret_fp] == ["none"]

    out = await api.put_pgp_passphrase(
        secret_fp, api.PgpPassphraseIn(passphrase=_PASSPHRASE), _admin(), session, settings
    )
    assert [p.source for p in out.pgp_passphrases if p.fingerprint == secret_fp] == ["tiqora"]
    assert _PASSPHRASE not in out.model_dump_json()
    stored = (
        await session.execute(
            text("SELECT value FROM tiqora_settings WHERE `key` = 'crypto.pgp.key_passwords'")
        )
    ).scalar_one()
    assert _PASSPHRASE not in stored

    # The resolved config can sign with it.
    cfg = await load_crypto_config(session, settings)
    assert PgpEngine.from_config(cfg.pgp).sign(b"x", secret_fp)

    # A wrong passphrase right after the right one is still rejected (agent cache).
    with pytest.raises(HTTPException) as exc:
        await api.put_pgp_passphrase(
            secret_fp, api.PgpPassphraseIn(passphrase="wrong"), _admin(), session, settings
        )
    assert exc.value.status_code == 422

    out = await api.delete_pgp_passphrase(secret_fp, _admin(), session, settings)
    assert [p.source for p in out.pgp_passphrases if p.fingerprint == secret_fp] == ["none"]
    actions = (
        (await session.execute(text("SELECT action FROM tiqora_crypto_key ORDER BY id")))
        .scalars()
        .all()
    )
    assert actions == ["passphrase_set", "passphrase_delete"]


async def test_passphrase_for_unknown_key_is_404(session: AsyncSession, settings: Settings) -> None:
    with pytest.raises(HTTPException) as exc:
        await api.put_pgp_passphrase(
            "DEADBEEF", api.PgpPassphraseIn(passphrase="x"), _admin(), session, settings
        )
    assert exc.value.status_code == 404
