"""Unit tests for Znuny-compatible password verification and hashing."""

from __future__ import annotations

import hashlib

import pytest
from passlib.hash import apr_md5_crypt, des_crypt, md5_crypt

from tiqora.znuny.password import detect_scheme, hash_password, verify_password

PASSWORD = "S3cret!pass"


def test_bcrypt_znuny_roundtrip() -> None:
    stored = hash_password(PASSWORD, cost=10)
    assert stored.startswith("BCRYPT:10:")
    parts = stored.split(":", 3)
    assert len(parts) == 4
    assert len(parts[2]) == 16
    assert verify_password(PASSWORD, stored)
    assert not verify_password("wrong", stored)
    assert detect_scheme(stored) == "bcrypt"


def test_bcrypt_fixture_constructed() -> None:
    # Fixed salt for a deterministic fixture
    from tiqora.znuny.password import _znuny_bcrypt_hash_part

    salt = "0123456789abcdef"
    hash_part = _znuny_bcrypt_hash_part(PASSWORD.encode("utf-8"), 10, salt)
    stored = f"BCRYPT:10:{salt}:{hash_part}"
    assert verify_password(PASSWORD, stored)


def test_sha256() -> None:
    stored = hashlib.sha256(PASSWORD.encode("utf-8")).hexdigest()
    assert len(stored) == 64
    assert verify_password(PASSWORD, stored)
    assert not verify_password("nope", stored)
    assert detect_scheme(stored) == "sha256"


def test_sha512() -> None:
    stored = hashlib.sha512(PASSWORD.encode("utf-8")).hexdigest()
    assert len(stored) == 128
    assert verify_password(PASSWORD, stored)
    assert detect_scheme(stored) == "sha512"


def test_sha1() -> None:
    stored = hashlib.sha1(PASSWORD.encode("utf-8")).hexdigest()  # noqa: S324
    assert len(stored) == 40
    assert verify_password(PASSWORD, stored)
    assert detect_scheme(stored) == "sha1"


def test_unix_md5_crypt() -> None:
    stored = md5_crypt.hash(PASSWORD)
    assert stored.startswith("$1$")
    assert verify_password(PASSWORD, stored)
    assert not verify_password("wrong", stored)
    assert detect_scheme(stored) == "unix_md5_crypt"


def test_apache_md5_crypt() -> None:
    stored = apr_md5_crypt.hash(PASSWORD)
    assert stored.startswith("$apr1$")
    assert verify_password(PASSWORD, stored)
    assert detect_scheme(stored) == "apache_md5_crypt"


def test_des_crypt() -> None:
    stored = des_crypt.hash(PASSWORD)
    assert len(stored) == 13
    assert verify_password(PASSWORD, stored)
    assert not verify_password("wrong", stored)
    assert detect_scheme(stored) == "crypt"


def test_plain() -> None:
    assert verify_password(PASSWORD, PASSWORD, crypt_type_plain=True)
    assert not verify_password(PASSWORD, "other", crypt_type_plain=True)


def test_empty_password_never_succeeds() -> None:
    stored = hash_password("x", cost=10)
    assert not verify_password("", stored)
    assert not verify_password("", "anything", crypt_type_plain=True)
    assert not verify_password(PASSWORD, "")


@pytest.mark.parametrize(
    ("stored", "scheme"),
    [
        ("", "empty"),
        ("not-a-hash", "unknown"),
    ],
)
def test_detect_scheme_edge(stored: str, scheme: str) -> None:
    assert detect_scheme(stored) == scheme
