"""Crypto config resolution (tiqora/crypto/config.py): Znuny SysConfig + env overrides."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from tiqora.config import Settings
from tiqora.crypto.config import CryptoConfig, backend_status_sync, resolve_crypto_config
from tiqora.znuny.sysconfig import SysConfig

_ZNUNY = {
    "PGP": "1",
    "PGP::Bin": "/nonexistent/gpg",
    "PGP::Options": "--homedir /opt/otrs/.gnupg/ --batch --no-tty --yes --trust-model always",
    "PGP::Key::Password": "---\nD2DF79FA: SomePassword\n488A0B8F: Other\n",
    "PGP::Options::DigestPreference": "sha512",
    "SMIME": "1",
    "SMIME::Bin": "/nonexistent/openssl",
    "SMIME::CertPath": "/etc/ssl/certs",
    "SMIME::PrivatePath": "/etc/ssl/private",
}


def _sysconfig(values: dict[str, Any]) -> SysConfig:
    async def fetch(name: str) -> Any:
        return values.get(name)

    return SysConfig(fetch=fetch)


async def test_sysconfig_values_are_used_without_env() -> None:
    cfg = await resolve_crypto_config(Settings(), _sysconfig(_ZNUNY))
    assert cfg.pgp.enabled is True
    assert cfg.pgp.homedir == "/opt/otrs/.gnupg/"
    assert cfg.pgp.options == ("--yes", "--trust-model", "always")
    assert cfg.pgp.passwords == {"D2DF79FA": "SomePassword", "488A0B8F": "Other"}
    assert cfg.pgp.digest == "sha512"
    # Znuny's binary path does not exist here → PATH lookup.
    assert cfg.pgp.gpg_bin == "gpg"
    assert cfg.smime.openssl_bin == "openssl"
    assert cfg.smime.enabled is True
    assert cfg.smime.cert_path == "/etc/ssl/certs"
    assert cfg.smime.private_path == "/etc/ssl/private"


async def test_env_overrides_paths_and_switches() -> None:
    settings = Settings(
        TIQORA_CRYPTO_PGP_ENABLED="0",
        TIQORA_CRYPTO_PGP_GNUPGHOME="/keys/gnupg",
        TIQORA_CRYPTO_SMIME_CERT_DIR="/keys/certs",
        TIQORA_CRYPTO_SMIME_PRIVATE_DIR="/keys/private",
        TIQORA_CRYPTO_OPENSSL_BIN="/usr/local/bin/openssl",
    )
    cfg = await resolve_crypto_config(settings, _sysconfig(_ZNUNY))
    assert cfg.pgp.enabled is False
    assert cfg.pgp.homedir == "/keys/gnupg"
    assert cfg.pgp.options == ("--yes", "--trust-model", "always")  # still from SysConfig
    assert cfg.smime.enabled is True
    assert cfg.smime.cert_path == "/keys/certs"
    assert cfg.smime.private_path == "/keys/private"
    assert cfg.smime.openssl_bin == "/usr/local/bin/openssl"


async def test_everything_off_without_sysconfig_rows() -> None:
    cfg = await resolve_crypto_config(Settings(), _sysconfig({}))
    assert cfg.pgp.enabled is False and cfg.smime.enabled is False
    assert cfg.pgp.homedir == "" and cfg.smime.cert_path == ""


async def test_no_sysconfig_is_env_only() -> None:
    cfg = await resolve_crypto_config(Settings(TIQORA_CRYPTO_SMIME_ENABLED="1"), None)
    assert cfg == CryptoConfig.from_settings(Settings(TIQORA_CRYPTO_SMIME_ENABLED="1"))
    assert cfg.smime.enabled is True


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl binary not on PATH")
async def test_backend_status_reports_problems(tmp_path: Path) -> None:
    settings = Settings(
        TIQORA_CRYPTO_SMIME_ENABLED="1",
        TIQORA_CRYPTO_SMIME_CERT_DIR=str(tmp_path),
        TIQORA_CRYPTO_GPG_BIN="/nonexistent/gpg",
    )
    by = {s.backend: s for s in backend_status_sync(CryptoConfig.from_settings(settings))}
    assert by["smime"].binary.available is True
    assert by["smime"].available is False  # private path not configured
    assert any("PrivatePath" in p for p in by["smime"].problems)
    assert by["pgp"].available is False
    assert any("gpg" in p for p in by["pgp"].problems)


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_pgp_status_flags_homedir_that_cannot_be_created(tmp_path: Path) -> None:
    locked = tmp_path / "locked"
    locked.mkdir(mode=0o500)
    try:
        settings = Settings(TIQORA_CRYPTO_PGP_GNUPGHOME=str(locked / "otrs" / ".gnupg"))
        by = {s.backend: s for s in backend_status_sync(CryptoConfig.from_settings(settings))}
        assert by["pgp"].available is False
        assert any("cannot be created" in p for p in by["pgp"].problems)
    finally:
        locked.chmod(0o700)


def test_pgp_status_accepts_missing_but_creatable_homedir(tmp_path: Path) -> None:
    settings = Settings(TIQORA_CRYPTO_PGP_GNUPGHOME=str(tmp_path / "new" / ".gnupg"))
    by = {s.backend: s for s in backend_status_sync(CryptoConfig.from_settings(settings))}
    assert not any("--homedir" in p for p in by["pgp"].problems)
