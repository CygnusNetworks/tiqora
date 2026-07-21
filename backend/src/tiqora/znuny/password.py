"""Znuny-compatible password hashing and verification.

Formats mirror ``Kernel/System/Auth/DB.pm`` (verify) and
``Kernel/System/User.pm`` (hash for rehash-on-login). Behaviour is ported;
no Znuny source is copied.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Final

import bcrypt
from passlib.hash import apr_md5_crypt, des_crypt, md5_crypt

# Default bcrypt work factor used by modern Znuny installs (AuthModule::DB::bcryptCost).
DEFAULT_BCRYPT_COST: Final[int] = 12

# bcrypt / eksblowfish base64 alphabet (same as Crypt::Eksblowfish::Bcrypt).
_BCRYPT_B64: Final[bytes] = b"./ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"


def _bcrypt_en_base64(data: bytes) -> str:
    """Encode *data* with OpenBSD/bcrypt base64 (MSB-first bit packing)."""
    # Same algorithm as OpenBSD encode_base64 / Crypt::Eksblowfish::Bcrypt::en_base64.
    alphabet = _BCRYPT_B64.decode("ascii")
    out: list[str] = []
    i = 0
    length = len(data)
    while i < length:
        c1 = data[i]
        i += 1
        out.append(alphabet[c1 >> 2])
        c1 = (c1 & 0x03) << 4
        if i >= length:
            out.append(alphabet[c1])
            break
        c2 = data[i]
        i += 1
        c1 |= (c2 >> 4) & 0x0F
        out.append(alphabet[c1])
        c1 = (c2 & 0x0F) << 2
        if i >= length:
            out.append(alphabet[c1])
            break
        c2 = data[i]
        i += 1
        c1 |= (c2 >> 6) & 0x03
        out.append(alphabet[c1])
        out.append(alphabet[c2 & 0x3F])
    return "".join(out)


def _password_bytes(password: str) -> bytes:
    """UTF-8 encode password (Znuny EncodeOutput strips the UTF-8 flag → octets)."""
    return password.encode("utf-8")


def _znuny_bcrypt_hash_part(password: bytes, cost: int, salt_ascii: str) -> str:
    """Return the 31-char bcrypt hash segment for Znuny ``BCRYPT:`` storage."""
    if len(salt_ascii) != 16:
        raise ValueError("Znuny bcrypt salt must be exactly 16 characters")
    salt_bytes = salt_ascii.encode("ascii")
    # Modular crypt salt: $2a$cost$ + 22-char en_base64(16-byte salt)
    salt_mcf = f"$2a${cost:02d}${_bcrypt_en_base64(salt_bytes)}".encode("ascii")
    hashed = bcrypt.hashpw(password, salt_mcf)
    # MCF payload after last $ is 22-char salt + 31-char hash
    payload = hashed.decode("ascii").rsplit("$", 1)[-1]
    return payload[22:]


def hash_password(password: str, *, cost: int = DEFAULT_BCRYPT_COST) -> str:
    """Return a Znuny ``BCRYPT:cost:salt:hash`` password string.

    Salt is 16 random alphanumeric characters (Znuny GenerateRandomString Length=16).
    Cost is clamped to 9..31 like Znuny.
    """
    cost = max(9, min(31, cost))
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    salt = "".join(secrets.choice(alphabet) for _ in range(16))
    hash_part = _znuny_bcrypt_hash_part(_password_bytes(password), cost, salt)
    return f"BCRYPT:{cost}:{salt}:{hash_part}"


def verify_password(password: str, stored: str, *, crypt_type_plain: bool = False) -> bool:
    """Return True if *password* matches Znuny-stored *stored* hash.

    When *crypt_type_plain* is True, behave as ``AuthModule::DB::CryptType = plain``.
    Empty passwords never succeed (Znuny Auth.pm).
    """
    if not password or not stored:
        return False

    if crypt_type_plain:
        return hmac.compare_digest(password, stored)

    pw = _password_bytes(password)

    # Non-DES modular / modern hashes (stored length != 13)
    if len(stored) != 13:
        # md5-crypt / apache md5-crypt: $id$salt$hash
        if stored.startswith("$") and stored.count("$") >= 3:
            return _verify_md5_crypt(password, stored)

        # sha256 hex
        if len(stored) == 64 and _is_hex(stored):
            digest = hashlib.sha256(pw).hexdigest()
            return hmac.compare_digest(digest.lower(), stored.lower())

        # sha512 hex
        if len(stored) == 128 and _is_hex(stored):
            digest = hashlib.sha512(pw).hexdigest()
            return hmac.compare_digest(digest.lower(), stored.lower())

        # Znuny BCRYPT:cost:salt:hash
        if stored.startswith("BCRYPT:"):
            return _verify_bcrypt_znuny(password, stored)

        # sha1 hex
        if len(stored) == 40 and _is_hex(stored):
            digest = hashlib.sha1(pw).hexdigest()  # noqa: S324 — legacy Znuny hashes
            return hmac.compare_digest(digest.lower(), stored.lower())

        return False

    # Classic 13-char DES crypt (salt = first 2 chars of stored)
    return _verify_des_crypt(password, stored)


def _is_hex(value: str) -> bool:
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _verify_bcrypt_znuny(password: str, stored: str) -> bool:
    parts = stored.split(":", 3)
    if len(parts) != 4:
        return False
    _, cost_s, salt, expected_hash = parts
    if len(salt) != 16:
        return False
    try:
        cost = int(cost_s)
        actual_hash = _znuny_bcrypt_hash_part(_password_bytes(password), cost, salt)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual_hash, expected_hash)


def _verify_md5_crypt(password: str, stored: str) -> bool:
    if stored.startswith("$apr1$"):
        try:
            return bool(apr_md5_crypt.verify(password, stored))
        except (ValueError, TypeError):
            return False
    if stored.startswith("$1$"):
        try:
            return bool(md5_crypt.verify(password, stored))
        except (ValueError, TypeError):
            return False
    try:
        return bool(md5_crypt.verify(password, stored))
    except (ValueError, TypeError):
        return False


def _verify_des_crypt(password: str, stored: str) -> bool:
    try:
        return bool(des_crypt.verify(password, stored))
    except (ValueError, TypeError):
        return False


def detect_scheme(stored: str) -> str:
    """Best-effort scheme name for logging (not used for auth decisions)."""
    if not stored:
        return "empty"
    if stored.startswith("BCRYPT:"):
        return "bcrypt"
    if stored.startswith("$apr1$"):
        return "apache_md5_crypt"
    if stored.startswith("$1$"):
        return "unix_md5_crypt"
    if len(stored) == 64 and _is_hex(stored):
        return "sha256"
    if len(stored) == 128 and _is_hex(stored):
        return "sha512"
    if len(stored) == 40 and _is_hex(stored):
        return "sha1"
    if len(stored) == 13:
        return "crypt"
    return "unknown"


# Schemes Znuny still verifies but that are weak for online/offline attack.
# BCRYPT: is the only modern write format we produce (Znuny-compatible).
_STRONG_SCHEMES: Final[frozenset[str]] = frozenset({"bcrypt"})


def is_strong_scheme(stored: str) -> bool:
    """Return True when *stored* is already a modern Znuny ``BCRYPT:`` hash."""
    return detect_scheme(stored) in _STRONG_SCHEMES


def is_weak_scheme(stored: str) -> bool:
    """Return True when *stored* is a legacy/weak scheme (or unknown/empty).

    Used for rehash-on-login and the optional reject-weak policy. Empty and
    unknown are treated as weak so they never count as "modern".
    """
    scheme = detect_scheme(stored)
    return scheme not in _STRONG_SCHEMES


def needs_rehash(stored: str) -> bool:
    """True when a successful verify should upgrade *stored* to ``BCRYPT:``."""
    return not is_strong_scheme(stored)


def rehash_password(password: str, *, cost: int = DEFAULT_BCRYPT_COST) -> str:
    """Hash *password* with Znuny ``BCRYPT:`` (alias of :func:`hash_password`)."""
    return hash_password(password, cost=cost)
