"""Ed25519 keys, package signatures and trust resolution (D2/D3).

Pure module: no database, no filesystem. Never logs key material.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

if TYPE_CHECKING:
    from tiqora.config import Settings

logger = logging.getLogger(__name__)

KEY_PREFIX: Final = "ed25519:"
# key_id -> "ed25519:..." (Assumption 1: none shipped yet)
OFFICIAL_KEYS: Final[Mapping[str, str]] = MappingProxyType({})

_KEY_ID_RE: Final = re.compile(r"[0-9a-f]{16}")
_SIG_KEYS: Final = frozenset({"format", "alg", "key_id", "signature"})


class SignatureError(ValueError):
    """Invalid key or signature; the message is German and shown to admins."""


class TrustLevel(StrEnum):
    OFFICIAL = "official"
    VERIFIED_PUBLISHER = "verified_publisher"
    LOCAL = "local"
    UNSIGNED = "unsigned"
    INVALID = "invalid"


#: the values of :class:`TrustLevel` as a ``Literal`` for API models (a test pins the equality)
TrustLevelName = Literal["official", "verified_publisher", "local", "unsigned", "invalid"]


def parse_public_key(text: str) -> Ed25519PublicKey:
    if not isinstance(text, str) or not text.startswith(KEY_PREFIX):
        raise SignatureError("Öffentlicher Schlüssel muss mit „ed25519:“ beginnen")
    try:
        raw = base64.b64decode(text[len(KEY_PREFIX) :], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SignatureError("Öffentlicher Schlüssel ist kein gültiges Base64") from exc
    if len(raw) != 32:
        raise SignatureError("Öffentlicher Schlüssel hat die falsche Länge")
    try:
        return Ed25519PublicKey.from_public_bytes(raw)
    except ValueError as exc:  # pragma: no cover - 32 bytes always load
        raise SignatureError("Öffentlicher Schlüssel ist ungültig") from exc


def _raw_public(key: Ed25519PublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def public_key_text(key: Ed25519PublicKey) -> str:
    return KEY_PREFIX + base64.b64encode(_raw_public(key)).decode("ascii")


def key_id(key: Ed25519PublicKey) -> str:
    return hashlib.sha256(_raw_public(key)).hexdigest()[:16]


def generate_keypair() -> tuple[bytes, str]:
    private = Ed25519PrivateKey.generate()
    pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return pem, public_key_text(private.public_key())


def load_private_key(pem: bytes) -> Ed25519PrivateKey:
    try:
        key = serialization.load_pem_private_key(pem, password=None)
    except (ValueError, TypeError) as exc:
        raise SignatureError("Privater Schlüssel konnte nicht gelesen werden") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise SignatureError("Privater Schlüssel ist kein Ed25519-Schlüssel")
    return key


def sign_bytes(private: Ed25519PrivateKey, data: bytes) -> bytes:
    payload = {
        "format": 1,
        "alg": "ed25519",
        "key_id": key_id(private.public_key()),
        "signature": base64.b64encode(private.sign(data)).decode("ascii"),
    }
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def parse_signature(raw: bytes) -> tuple[str, bytes]:
    broken = SignatureError("Signatur ist beschädigt")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise broken from exc
    if not isinstance(data, dict) or set(data) != _SIG_KEYS:
        raise broken
    fmt = data["format"]
    if type(fmt) is not int or fmt != 1 or data["alg"] != "ed25519":
        raise broken
    kid, sig_b64 = data["key_id"], data["signature"]
    if not isinstance(kid, str) or not _KEY_ID_RE.fullmatch(kid) or not isinstance(sig_b64, str):
        raise broken
    try:
        sig = base64.b64decode(sig_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise broken from exc
    if len(sig) != 64:
        raise broken
    return kid, sig


def verify_signature(raw_sig: bytes, data: bytes, key: Ed25519PublicKey) -> None:
    _, sig = parse_signature(raw_sig)
    try:
        key.verify(sig, data)
    except InvalidSignature as exc:
        raise SignatureError("Signatur ungültig") from exc


def local_keys(settings: Settings) -> dict[str, Ed25519PublicKey]:
    out: dict[str, Ed25519PublicKey] = {}
    for entry in settings.plugins_trusted_keys.split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            key = parse_public_key(entry)
        except SignatureError as exc:
            logger.warning("TIQORA_PLUGINS_TRUSTED_KEYS: Eintrag übersprungen (%s)", exc)
            continue
        out[key_id(key)] = key
    return out


@dataclass(frozen=True, slots=True)
class PublisherKey:
    key_id: str
    public_key: Ed25519PublicKey
    publisher_id: str
    publisher_name: str
    repository_id: int
    revoked: bool


@dataclass(frozen=True, slots=True)
class KeyRing:
    official: Mapping[str, Ed25519PublicKey]
    local: Mapping[str, Ed25519PublicKey]
    publishers: Sequence[PublisherKey]  # only from VERIFIED indexes (D7)
    #: F10: repository ids whose publisher keys may sign this package (``None`` = any). The
    #: cross-repository checks (revocation, conflicting claims) always see every publisher key.
    signer_repositories: frozenset[int] | None = None


@dataclass(frozen=True, slots=True)
class TrustResult:
    level: TrustLevel
    key_id: str | None
    signer: str | None
    problem: str | None


def _invalid(kid: str | None, problem: str) -> TrustResult:
    return TrustResult(TrustLevel.INVALID, kid, None, problem)


def resolve_trust(
    signature: bytes | None,
    checksums: bytes,
    *,
    plugin_id: str,
    manifest_publisher_id: str | None,
    ring: KeyRing,
) -> TrustResult:
    if signature is None:
        return TrustResult(TrustLevel.UNSIGNED, None, None, None)
    try:
        kid, _ = parse_signature(signature)
    except SignatureError as exc:
        return _invalid(None, str(exc))

    def verified(key: Ed25519PublicKey) -> str | None:
        try:
            verify_signature(signature, checksums, key)
        except SignatureError as exc:
            return str(exc)
        return None

    if kid in ring.official:
        err = verified(ring.official[kid])
        if err:
            return _invalid(kid, err)
        return TrustResult(TrustLevel.OFFICIAL, kid, "Tiqora", None)
    if kid in ring.local:
        err = verified(ring.local[kid])
        if err:
            return _invalid(kid, err)
        return TrustResult(TrustLevel.LOCAL, kid, f"Eigener Schlüssel ({kid})", None)

    allowed = ring.signer_repositories
    if not any(
        p.key_id == kid and (allowed is None or p.repository_id in allowed) for p in ring.publishers
    ):
        return TrustResult(
            TrustLevel.UNSIGNED, kid, None, "Signiert mit einem unbekannten Schlüssel"
        )
    # every repository's claim on the key counts from here on, not only the source's
    matches = [p for p in ring.publishers if p.key_id == kid]
    # The outcome must not depend on ring order: one key claimed for different
    # publishers is a conflict, never "first entry wins".
    entry = min(matches, key=lambda p: (p.publisher_id, p.publisher_name, p.repository_id))
    err = verified(entry.public_key)
    if err:
        return _invalid(kid, err)
    if any(p.revoked for p in matches):
        return _invalid(kid, REVOKED_MESSAGE)
    contested = _contested(ring, kid)
    if contested:
        return TrustResult(TrustLevel.UNSIGNED, kid, None, contested)
    if manifest_publisher_id == entry.publisher_id and plugin_id.startswith(
        entry.publisher_id + "."
    ):
        return TrustResult(TrustLevel.VERIFIED_PUBLISHER, kid, entry.publisher_name, None)
    return TrustResult(
        TrustLevel.UNSIGNED,
        kid,
        None,
        "Signiert mit einem Schlüssel, der nicht zu diesem Herausgeber gehört",
    )


REVOKED_MESSAGE: Final = "Schlüssel des Herausgebers wurde zurückgezogen"
MULTI_PUBLISHER_MESSAGE: Final = "Schlüssel wird für verschiedene Herausgeber beansprucht"
MULTI_REPOSITORY_MESSAGE: Final = (
    "Mehrere Paketquellen beanspruchen diesen Herausgeber mit unterschiedlichen Schlüsseln"
)


def _contested(ring: KeyRing, kid: str) -> str | None:
    """Why the publisher key ``kid`` gives no "verified" label although it is not revoked.

    One key claimed for different publishers is a conflict. F10, per repository: every
    repository that claims the key's publisher id must list the key; a repository that claims
    the id but not the key makes the id unverified. Several keys inside one repository
    (rotation) are fine. Always judged on the whole ring, whichever repository is the source.
    """
    matches = [p for p in ring.publishers if p.key_id == kid]
    if len({(p.publisher_id, p.publisher_name) for p in matches}) > 1:
        return MULTI_PUBLISHER_MESSAGE
    publisher_id = matches[0].publisher_id
    claiming = {
        p.repository_id for p in ring.publishers if p.publisher_id == publisher_id and not p.revoked
    }
    listing = {p.repository_id for p in matches if not p.revoked}
    if claiming - listing:
        return MULTI_REPOSITORY_MESSAGE
    return None


def publisher_verified_in(ring: KeyRing, publisher_id: str, repository_id: int) -> bool:
    """Catalog badge (F10): would a package of ``publisher_id`` signed with one of
    ``repository_id``'s keys be rated "verifizierter Herausgeber"?

    True when the repository lists at least one key for the publisher that is revoked nowhere
    and not contested (see :func:`_contested`), i.e. the same rules :func:`resolve_trust`
    applies to a download from that repository.
    """
    for p in ring.publishers:
        if p.repository_id != repository_id or p.publisher_id != publisher_id or p.revoked:
            continue
        if any(q.revoked for q in ring.publishers if q.key_id == p.key_id):
            continue
        if _contested(ring, p.key_id) is None:
            return True
    return False
