"""PGP import audit trail (tiqora/crypto/keystore.py import_pgp_key) — records
a ``tiqora_crypto_key`` row per imported fingerprint. DB-marked (needs the
table).
"""

from __future__ import annotations

import contextlib
import shutil
import tempfile

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.crypto.keystore import import_pgp_key
from tiqora.crypto.pgp import PgpEngine
from tiqora.db.tiqora.models import TiqoraCryptoKey

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(shutil.which("gpg") is None, reason="gpg binary not on PATH"),
]


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


async def _seed_table(session: AsyncSession) -> None:
    with contextlib.suppress(Exception):
        await session.execute(
            text(
                "CREATE TABLE IF NOT EXISTS tiqora_crypto_key ("
                "id INT AUTO_INCREMENT PRIMARY KEY, key_type VARCHAR(20) NOT NULL,"
                " identifier VARCHAR(255) NOT NULL, email VARCHAR(255),"
                " purpose VARCHAR(20) NOT NULL DEFAULT 'both',"
                " has_private_key TINYINT(1) NOT NULL DEFAULT 0,"
                " created DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
        )
    await session.commit()
    with contextlib.suppress(Exception):
        await session.execute(text("DELETE FROM tiqora_crypto_key"))
        await session.commit()


@pytest.mark.asyncio
async def test_import_pgp_key_records_audit_row(mariadb_znuny_url: str) -> None:
    with tempfile.TemporaryDirectory(dir="/tmp") as gnupghome:  # noqa: S108
        import gnupg

        gpg = gnupg.GPG(gnupghome=gnupghome)
        gpg.encoding = "utf-8"
        input_data = gpg.gen_key_input(
            name_email="import-test@example.com",
            passphrase="",
            key_type="RSA",
            key_length=2048,
            no_protection=True,
        )
        gen = gpg.gen_key(input_data)
        assert gen.fingerprint
        exported = gpg.export_keys(gen.fingerprint)

        with tempfile.TemporaryDirectory(dir="/tmp") as other_home:  # noqa: S108
            engine = create_async_engine(_mysql_async(mariadb_znuny_url))
            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with factory() as session:
                await _seed_table(session)

            pgp_engine = PgpEngine(other_home)
            async with factory() as session:
                fingerprints = await import_pgp_key(
                    session, pgp_engine, exported, email="import-test@example.com", purpose="sign"
                )
            assert gen.fingerprint in fingerprints

            async with factory() as session:
                row = (
                    await session.execute(
                        select(TiqoraCryptoKey).where(TiqoraCryptoKey.identifier == gen.fingerprint)
                    )
                ).scalar_one()
                assert row.key_type == "pgp"
                assert row.email == "import-test@example.com"
                assert row.purpose == "sign"
                assert row.has_private_key is False
            await engine.dispose()
