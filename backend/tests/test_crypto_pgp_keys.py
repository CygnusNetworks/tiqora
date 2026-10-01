"""PGP key management (tiqora/crypto/pgp.py) against a real, throwaway gpg keyring.

Listing with parsed metadata, Znuny-style ids, ``PGP::Key::Password``
passphrase lookup, delete (secret only / whole key), export, and the
``PGP::Options`` parser. ``GNUPGHOME`` lives directly under ``/tmp`` (gpg-agent
socket path limit on macOS, see test_crypto_pgp.py).
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("gnupg")

from tiqora.crypto import CryptoError, CryptoUnavailableError
from tiqora.crypto.config import parse_pgp_options
from tiqora.crypto.pgp import PgpEngine, normalize_key_ref

pytestmark = pytest.mark.skipif(shutil.which("gpg") is None, reason="gpg binary not on PATH")

_NAME = "Erika Beispiel"


@pytest.fixture
def gnupghome() -> Iterator[str]:
    with tempfile.TemporaryDirectory(dir="/tmp") as d:  # noqa: S108 — gpg-agent socket path limit
        yield d


def _gen(
    engine: PgpEngine,
    email: str,
    *,
    passphrase: str = "",
    options: list[str] | None = None,
    expire: str = "0",
) -> str:
    import gnupg

    gpg = gnupg.GPG(gnupghome=engine.gnupghome, options=options)
    params: dict[str, object] = {
        "name_real": _NAME,
        "name_email": email,
        "key_type": "RSA",
        "key_length": 2048,
        "subkey_type": "RSA",
        "subkey_length": 2048,
        "expire_date": expire,
    }
    if passphrase:
        params["passphrase"] = passphrase
    else:
        params["no_protection"] = True
    key = gpg.gen_key(gpg.gen_key_input(**params))
    assert key.fingerprint, key.stderr
    return str(key.fingerprint)


def test_list_keys_parses_metadata_and_secret_flag(gnupghome: str) -> None:
    engine = PgpEngine(gnupghome)
    fp = _gen(engine, "Alice@Example.org")

    keys = engine.list_keys()
    assert len(keys) == 1
    key = keys[0]
    assert key.fingerprint == fp
    assert key.key_id == fp[-16:]
    assert key.short_id == fp[-8:]
    assert key.emails == ["alice@example.org"]
    assert key.uids == [f"{_NAME} <Alice@Example.org>"]
    assert key.status == "good"
    assert key.has_secret is True
    assert key.bits == 2048
    assert key.algorithm == "RSA"
    assert key.created is not None
    assert key.expires is None
    assert len(key.subkey_ids) == 1
    # Znuny shows the last subkey's short id for secret keys.
    assert key.znuny_key_id == key.subkey_ids[0][-8:]


def test_expired_key_is_reported_expired(gnupghome: str) -> None:
    engine = PgpEngine(gnupghome)
    _gen(engine, "old@example.org", options=["--faked-system-time", "20200101T000000"], expire="1d")
    (key,) = engine.list_keys()
    assert key.status == "expired"
    assert key.expires is not None


def test_find_key_accepts_any_id_form(gnupghome: str) -> None:
    engine = PgpEngine(gnupghome)
    fp = _gen(engine, "find@example.org")
    key = engine.find_key(fp)
    assert key is not None
    for ref in (fp.lower(), fp[-16:], "0x" + fp[-8:], key.subkey_ids[0], key.subkey_ids[0][-8:]):
        found = engine.find_key(ref)
        assert found is not None and found.fingerprint == fp, ref
    assert engine.find_key("DEADBEEF") is None


def test_search_by_email_secret_only(gnupghome: str) -> None:
    engine = PgpEngine(gnupghome)
    fp = _gen(engine, "queue@example.org")
    assert [k.fingerprint for k in engine.search("QUEUE@example.org", secret=True)] == [fp]
    assert engine.search("other@example.org") == []


def test_sign_uses_znuny_password_hash(gnupghome: str) -> None:
    fp = _gen(PgpEngine(gnupghome), "pw@example.org", passphrase="s3cret")
    probe = PgpEngine(gnupghome).find_key(fp)
    assert probe is not None
    # Keyed by the 8-hex id Znuny shows in AdminPGP, lower case on purpose.
    engine = PgpEngine(gnupghome, passwords={probe.znuny_key_id.lower(): "s3cret"})
    assert engine.passphrase_for(fp) == "s3cret"
    signature = engine.sign(b"payload", probe.znuny_key_id)
    assert b"BEGIN PGP SIGNATURE" in signature


def test_sign_with_wrong_password_fails(gnupghome: str) -> None:
    fp = _gen(PgpEngine(gnupghome), "pw2@example.org", passphrase="right")
    engine = PgpEngine(gnupghome, passwords={fp: "wrong"})
    with pytest.raises(CryptoError):
        engine.sign(b"payload", fp)


def test_decrypt_tries_configured_passwords(gnupghome: str) -> None:
    fp = _gen(PgpEngine(gnupghome), "dec@example.org", passphrase="pass-1")
    engine = PgpEngine(gnupghome, passwords={"AAAAAAAA": "nope", fp[-8:]: "pass-1"})
    ciphertext = engine.encrypt(b"top secret", [fp])
    result = engine.decrypt(ciphertext)
    assert result.ok is True
    assert result.plaintext == b"top secret"


def test_digest_preference_is_applied(gnupghome: str) -> None:
    fp = _gen(PgpEngine(gnupghome), "digest@example.org")
    engine = PgpEngine(gnupghome, digest="sha512")
    sig = engine.sign(b"payload", fp, clearsign=True)
    assert b"Hash: SHA512" in sig


def test_export_public_returns_armored_public_key_only(gnupghome: str) -> None:
    engine = PgpEngine(gnupghome)
    fp = _gen(engine, "exp@example.org")
    armored = engine.export_public(fp[-8:])
    assert "BEGIN PGP PUBLIC KEY BLOCK" in armored
    assert "PRIVATE KEY" not in armored


def test_delete_secret_only_keeps_public(gnupghome: str) -> None:
    fp = _gen(PgpEngine(gnupghome), "del@example.org", passphrase="pp")
    engine = PgpEngine(gnupghome, passwords={fp: "pp"})
    engine.delete_key(fp, secret_only=True)
    (key,) = engine.list_keys()
    assert key.has_secret is False
    with pytest.raises(CryptoError):
        engine.delete_key(fp, secret_only=True)


def test_delete_whole_key_removes_secret_and_public(gnupghome: str) -> None:
    engine = PgpEngine(gnupghome)
    fp = _gen(engine, "gone@example.org")
    engine.delete_key(fp)
    assert engine.list_keys() == []
    with pytest.raises(CryptoError):
        engine.delete_key(fp)


def test_import_secret_key_returns_unique_fingerprints(gnupghome: str) -> None:
    source = PgpEngine(gnupghome)
    fp = _gen(source, "imp@example.org")
    import gnupg

    gpg = gnupg.GPG(gnupghome=gnupghome)
    armored = gpg.export_keys(fp, secret=True, expect_passphrase=False)
    with tempfile.TemporaryDirectory(dir="/tmp") as other:  # noqa: S108
        target = PgpEngine(other)
        assert target.import_key(armored) == [fp]
        (key,) = target.list_keys()
        assert key.has_secret is True


def test_parse_pgp_options_extracts_homedir() -> None:
    assert parse_pgp_options("--homedir /opt/otrs/.gnupg/ --batch --no-tty --yes") == (
        "/opt/otrs/.gnupg/",
        ("--yes",),
    )
    assert parse_pgp_options("--homedir=/x --trust-model always") == (
        "/x",
        ("--trust-model", "always"),
    )
    assert parse_pgp_options("") == ("", ())


def test_trust_model_option_is_passed_to_gpg(gnupghome: str) -> None:
    fp = _gen(PgpEngine(gnupghome), "trust@example.org")
    engine = PgpEngine(gnupghome, options=["--trust-model", "always"])
    assert engine.find_key(fp) is not None
    # A bogus option makes gpg fail: proves the options reach the binary.
    bad = PgpEngine(gnupghome, options=["--no-such-option-xyz"])
    with pytest.raises(CryptoUnavailableError):
        bad.list_keys()


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_uncreatable_homedir_is_unavailable_not_oserror(tmp_path: Path) -> None:
    # Prod: PGP::Options --homedir /opt/otrs/.gnupg, but the container has no
    # /opt/otrs and runs unprivileged -> mkdir raised PermissionError (HTTP 500).
    locked = tmp_path / "locked"
    locked.mkdir(mode=0o500)
    try:
        with pytest.raises(CryptoUnavailableError, match="homedir"):
            PgpEngine(str(locked / "otrs" / ".gnupg")).list_keys()
    finally:
        locked.chmod(0o700)


def test_normalize_key_ref() -> None:
    assert normalize_key_ref("0xdeadbeef") == "DEADBEEF"
    assert normalize_key_ref("AB12 CD34 EF56 7890") == "AB12CD34EF567890"
