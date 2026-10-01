"""PGP: key management + sign/verify/encrypt/decrypt via the ``gpg`` binary.

Mirrors Znuny's ``Kernel::System::Crypt::PGP``, which shells out to
``PGP::Bin`` with ``PGP::Options`` — same approach here, via ``python-gnupg``
rather than hand-rolled subprocess/regex parsing of gpg output. The keyring
is the one from ``PGP::Options --homedir`` (or ``TIQORA_CRYPTO_PGP_GNUPGHOME``,
see :mod:`tiqora.crypto.config`), so Tiqora and Znuny can share it.

Passphrases come from Znuny's ``PGP::Key::Password`` hash (key id →
passphrase). Znuny keys that hash by the 8-hex-digit id it shows in its UI
(for secret keys that is the *last subkey's* short id); :meth:`PgpEngine.
passphrase_for` therefore accepts any id of the key — fingerprint, long or
short primary id, long or short subkey id — case-insensitively.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tiqora.crypto import CryptoError, CryptoNotFoundError, CryptoUnavailableError

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z0-9-]{2,63}")
_HEX_RE = re.compile(r"^(?:0x)?([0-9A-Fa-f]{8,40})$")


@dataclass(frozen=True)
class PgpVerifyStatus:
    valid: bool
    fingerprint: str | None
    username: str | None
    status: str
    key_id: str | None = None
    trust_level: int | None = None


@dataclass(frozen=True)
class PgpDecryptResult:
    ok: bool
    plaintext: bytes
    verify: PgpVerifyStatus | None
    status: str


@dataclass(frozen=True)
class PgpKeyInfo:
    """Parsed metadata of one keyring entry (primary key + its subkeys)."""

    fingerprint: str
    key_id: str  # 16-hex long id of the primary key
    short_id: str  # last 8 hex digits of key_id
    uids: list[str]
    emails: list[str]
    created: datetime | None
    expires: datetime | None
    status: str  # "good" | "expired" | "revoked"
    has_secret: bool
    bits: int | None
    algorithm: str
    subkey_ids: list[str] = field(default_factory=list)

    @property
    def znuny_key_id(self) -> str:
        """The 8-hex id Znuny shows for this key (``_ParseGPGKeyList``).

        For secret keys Znuny overwrites the id with every ``ssb`` line, so it
        ends up as the last subkey's short id; public keys keep the primary id.
        The queue ``default_sign_key`` values (``PGP::Detached::<id>``) and the
        ``PGP::Key::Password`` keys use this id.
        """
        if self.has_secret and self.subkey_ids:
            return self.subkey_ids[-1][-8:].upper()
        return self.short_id

    def all_ids(self) -> set[str]:
        ids = {self.fingerprint.upper(), self.key_id.upper(), self.short_id.upper()}
        for sub in self.subkey_ids:
            ids.add(sub.upper())
            ids.add(sub[-8:].upper())
        return ids

    def matches(self, key_ref: str) -> bool:
        ref = normalize_key_ref(key_ref)
        if not ref:
            return False
        return ref in self.all_ids() or self.fingerprint.upper().endswith(ref)


def normalize_key_ref(key_ref: str) -> str:
    """Strip ``0x``/spaces from a key id or fingerprint and upper-case it."""
    compact = (key_ref or "").replace(" ", "").strip()
    m = _HEX_RE.match(compact)
    return m.group(1).upper() if m else compact.upper()


def _require_gnupg() -> Any:
    try:
        import gnupg
    except ImportError as exc:
        raise CryptoUnavailableError(
            "python-gnupg is required for PGP support but is not installed. "
            "It ships in the backend 'crypto' extra — run "
            "`uv sync --extra crypto` (or `uv sync --all-extras`)."
        ) from exc
    return gnupg


def _epoch(value: Any) -> datetime | None:
    try:
        num = int(str(value))
    except (TypeError, ValueError):
        return None
    if num <= 0:
        return None
    return datetime.fromtimestamp(num, tz=UTC)


def _emails(uids: list[str]) -> list[str]:
    found: list[str] = []
    for uid in uids:
        for m in _EMAIL_RE.findall(uid):
            low = m.lower()
            if low not in found:
                found.append(low)
    return found


#: OpenPGP hash algorithm ids (RFC 4880 §9.4) → RFC 3156 ``micalg`` values.
_MICALG = {
    "1": "pgp-md5",
    "2": "pgp-sha1",
    "3": "pgp-ripemd160",
    "8": "pgp-sha256",
    "9": "pgp-sha384",
    "10": "pgp-sha512",
    "11": "pgp-sha224",
}

_ALGOS = {"1": "RSA", "16": "ElGamal", "17": "DSA", "18": "ECDH", "19": "ECDSA", "22": "EdDSA"}


def _key_info(raw: Any, secret_fps: set[str], now: datetime) -> PgpKeyInfo:
    """One python-gnupg key dict (``list_keys`` / ``scan_keys_mem``) → :class:`PgpKeyInfo`."""
    uids = [str(u) for u in raw.get("uids") or []]
    expires = _epoch(raw.get("expires"))
    trust = str(raw.get("trust") or "")
    if trust == "r":
        status = "revoked"
    elif trust == "e" or (expires is not None and expires <= now):
        status = "expired"
    else:
        status = "good"
    try:
        bits: int | None = int(str(raw.get("length")))
    except (TypeError, ValueError):
        bits = None
    key_id = str(raw.get("keyid") or "").upper()
    fp = str(raw.get("fingerprint") or "").upper()
    return PgpKeyInfo(
        fingerprint=fp,
        key_id=key_id,
        short_id=key_id[-8:],
        uids=uids,
        emails=_emails(uids),
        created=_epoch(raw.get("date")),
        expires=expires,
        status=status,
        has_secret=fp in secret_fps,
        bits=bits,
        algorithm=_ALGOS.get(str(raw.get("algo") or ""), str(raw.get("algo") or "")),
        subkey_ids=[str(s[0]).upper() for s in raw.get("subkeys") or [] if s],
    )


def scan_armored_keys(armored: str, *, gpg_bin: str = "gpg") -> list[PgpKeyInfo]:
    """Parse the keys in an ASCII-armored block without touching any keyring.

    Runs gpg against a throwaway ``--homedir`` (``--import-options show-only``
    via python-gnupg's ``scan_keys_mem``), so it works even when no Tiqora
    keyring is configured and never writes to the shared one. Raises
    :class:`CryptoUnavailableError` when python-gnupg or the binary is missing.
    """
    gnupg = _require_gnupg()
    # Directly under /tmp when possible: gpg's socket dir must stay short on macOS.
    base = "/tmp" if Path("/tmp").is_dir() else None  # noqa: S108
    with tempfile.TemporaryDirectory(prefix="tq-scan-", dir=base) as home:
        try:
            gpg = gnupg.GPG(
                gpgbinary=gpg_bin or "gpg",
                gnupghome=home,
                env={**os.environ, "LC_MESSAGES": "POSIX"},
            )
        except (OSError, ValueError, RuntimeError) as exc:
            raise CryptoUnavailableError(f"gpg not usable ({gpg_bin!r}): {exc}") from exc
        gpg.encoding = "utf-8"
        now = datetime.now(UTC)
        return [_key_info(raw, set(), now) for raw in gpg.scan_keys_mem(armored)]


class PgpEngine:
    """Thin, testable wrapper around a ``python-gnupg`` GPG instance.

    One instance per keyring; each call opens its own ``gnupg.GPG()`` (cheap —
    python-gnupg shells out per call anyway) so no long-lived subprocess state.
    """

    def __init__(
        self,
        gnupghome: str,
        *,
        gpg_bin: str = "gpg",
        options: tuple[str, ...] | list[str] = (),
        passwords: dict[str, str] | None = None,
        digest: str = "",
        trusted_network: bool = True,
    ) -> None:
        if not gnupghome:
            raise CryptoUnavailableError(
                "PGP keyring (GNUPGHOME / PGP::Options --homedir) is not configured"
            )
        self._gnupghome = gnupghome
        self._gpg_bin = gpg_bin or "gpg"
        self._options = list(options)
        self._passwords = {normalize_key_ref(k): v for k, v in (passwords or {}).items()}
        self._digest = digest
        #: ``PGP::TrustedNetwork`` — default for ``always_trust`` on encrypt/decrypt.
        self._always_trust = trusted_network

    @classmethod
    def from_config(cls, cfg: Any) -> PgpEngine:
        """Build from a :class:`tiqora.crypto.config.PgpConfig`."""
        return cls(
            cfg.homedir,
            gpg_bin=cfg.gpg_bin,
            options=cfg.options,
            passwords=cfg.passwords,
            digest=cfg.digest,
            trusted_network=getattr(cfg, "trusted_network", True),
        )

    @property
    def gnupghome(self) -> str:
        return self._gnupghome

    def _gpg(self) -> Any:
        gnupg = _require_gnupg()
        home = Path(self._gnupghome)
        if not home.exists():
            try:
                home.mkdir(parents=True, exist_ok=True)
                home.chmod(0o700)
            except OSError as exc:
                raise CryptoUnavailableError(
                    f"PGP keyring (--homedir) {self._gnupghome} cannot be created: {exc}"
                ) from exc
        try:
            gpg = gnupg.GPG(
                gpgbinary=self._gpg_bin,
                gnupghome=self._gnupghome,
                options=self._options or None,
                env={**os.environ, "LC_MESSAGES": "POSIX"},
            )
        except (OSError, ValueError, RuntimeError) as exc:
            raise CryptoUnavailableError(f"gpg not usable ({self._gpg_bin!r}): {exc}") from exc
        gpg.encoding = "utf-8"
        return gpg

    # ------------------------------------------------------------------ keys

    def import_key(self, key_data: str) -> list[str]:
        """Import an ASCII-armored public or private key. Returns fingerprints."""
        result = self._gpg().import_keys(key_data)
        if not result.fingerprints:
            raise CryptoError(f"PGP key import failed: {result.stderr}")
        # A secret key import lists the fingerprint twice (pub + sec).
        return list(dict.fromkeys(str(fp) for fp in result.fingerprints))

    def list_key_fingerprints(self, *, secret: bool = False) -> list[str]:
        keys = self._gpg().list_keys(secret)
        return [str(k["fingerprint"]) for k in keys]

    def list_keys(self) -> list[PgpKeyInfo]:
        """All keys in the keyring with parsed metadata (public + secret merged)."""
        gpg = self._gpg()
        secret_fps = {str(k["fingerprint"]).upper() for k in gpg.list_keys(True)}
        now = datetime.now(UTC)
        return [_key_info(raw, secret_fps, now) for raw in gpg.list_keys(False)]

    def find_key(self, key_ref: str) -> PgpKeyInfo | None:
        for key in self.list_keys():
            if key.matches(key_ref):
                return key
        return None

    def search(self, email: str, *, secret: bool = False) -> list[PgpKeyInfo]:
        """Keys whose uids contain *email* (Znuny ``PublicKeySearch``/``PrivateKeySearch``)."""
        needle = email.strip().lower()
        return [k for k in self.list_keys() if needle in k.emails and (k.has_secret or not secret)]

    def delete_key(self, key_ref: str, *, secret_only: bool = False) -> PgpKeyInfo:
        """Delete a key. ``secret_only`` keeps the public part (Znuny ``SecretKeyDelete``).

        Deleting the whole key removes the secret part first — gpg refuses to
        delete a public key while its secret key is still present.
        """
        key = self.find_key(key_ref)
        if key is None:
            raise CryptoNotFoundError(f"PGP key {key_ref!r} not found")
        gpg = self._gpg()
        if key.has_secret:
            res = gpg.delete_keys(
                key.fingerprint, secret=True, passphrase=self.passphrase_for(key) or ""
            )
            if str(res.status) != "ok":
                raise CryptoError(f"PGP secret key delete failed: {res.status} {res.stderr[-300:]}")
        elif secret_only:
            raise CryptoError(f"PGP key {key_ref!r} has no secret key")
        if not secret_only:
            res = gpg.delete_keys(key.fingerprint)
            if str(res.status) != "ok":
                raise CryptoError(f"PGP key delete failed: {res.status} {res.stderr[-300:]}")
        return key

    def export_public(self, key_ref: str) -> str:
        key = self.find_key(key_ref)
        if key is None:
            raise CryptoNotFoundError(f"PGP key {key_ref!r} not found")
        armored = self._gpg().export_keys(key.fingerprint)
        if not armored:
            raise CryptoError(f"PGP key export failed for {key_ref!r}")
        return str(armored)

    def passphrase_for(self, key: PgpKeyInfo | str) -> str | None:
        """Passphrase from ``PGP::Key::Password`` for any id of *key*."""
        if not self._passwords:
            return None
        if isinstance(key, str):
            ref = normalize_key_ref(key)
            if ref in self._passwords:
                return self._passwords[ref]
            found = self.find_key(key)
            if found is None:
                return None
            key = found
        for ident in [key.znuny_key_id, *sorted(key.all_ids())]:
            if ident in self._passwords:
                return self._passwords[ident]
        return None

    def _flush_agent_cache(self) -> None:
        """Make gpg-agent forget cached passphrases (``SIGHUP`` via gpgconf)."""
        gpgconf = shutil.which("gpgconf", path=os.path.dirname(shutil.which(self._gpg_bin) or ""))
        gpgconf = gpgconf or shutil.which("gpgconf")
        if not gpgconf:
            return
        subprocess.run(  # noqa: S603 — fixed arg list
            [gpgconf, "--homedir", self._gnupghome, "--reload", "gpg-agent"],
            capture_output=True,
            check=False,
            timeout=10,
        )

    def check_passphrase(self, key_ref: str, passphrase: str) -> PgpKeyInfo:
        """Verify *passphrase* for the secret key *key_ref* with a test signature.

        The agent cache is flushed first, otherwise a passphrase entered
        correctly a moment ago would let a wrong one pass.
        """
        key = self.find_key(key_ref)
        if key is None or not key.has_secret:
            raise CryptoNotFoundError(f"PGP secret key {key_ref!r} not found")
        self._flush_agent_cache()
        try:
            signed = self._gpg().sign(
                b"tiqora passphrase check",
                keyid=key.fingerprint,
                passphrase=passphrase,
                detach=True,
            )
        finally:
            self._flush_agent_cache()
        if not signed.data:
            raise CryptoError("wrong passphrase for this PGP key")
        return key

    # ------------------------------------------------------------- operations

    def _digest_args(self) -> list[str]:
        return ["--personal-digest-preferences", self._digest.upper()] if self._digest else []

    def sign(
        self,
        data: bytes,
        key_id: str,
        *,
        passphrase: str | None = None,
        clearsign: bool = False,
    ) -> bytes:
        """Detached (default) or clear-signed ASCII-armored signature over *data*."""
        if passphrase is None:
            passphrase = self.passphrase_for(key_id)
        signed = self._gpg().sign(
            data,
            keyid=key_id,
            detach=not clearsign,
            clearsign=clearsign,
            passphrase=passphrase,
            extra_args=self._digest_args() or None,
        )
        if not signed:
            raise CryptoError(f"PGP sign failed: {getattr(signed, 'stderr', '')}")
        return bytes(signed.data)

    def sign_detached(self, data: bytes, key_id: str) -> tuple[bytes, str]:
        """Armored detached signature over *data* plus its RFC 3156 ``micalg``.

        The digest is whatever gpg picked (``PGP::Options::DigestPreference``,
        key preferences); ``SIG_CREATED`` reports it, so the ``micalg``
        parameter always matches the signature — Znuny makes a dummy
        clear-signature just to read its ``Hash:`` line.
        """
        signed = self._gpg().sign(
            data,
            keyid=key_id,
            detach=True,
            clearsign=False,
            passphrase=self.passphrase_for(key_id),
            extra_args=self._digest_args() or None,
        )
        if not signed or not signed.data:
            raise CryptoError(
                f"PGP sign failed: {signed.status or ''} {getattr(signed, 'stderr', '')[-300:]}"
            )
        micalg = _MICALG.get(str(signed.hash_algo or ""), "pgp-sha256")
        return bytes(signed.data), micalg

    def encrypt(
        self,
        data: bytes,
        recipients: list[str],
        *,
        sign_key_id: str | None = None,
        passphrase: str | None = None,
        always_trust: bool | None = None,
        armor: bool = True,
    ) -> bytes:
        if sign_key_id and passphrase is None:
            passphrase = self.passphrase_for(sign_key_id)
        if always_trust is None:
            always_trust = self._always_trust
        result = self._gpg().encrypt(
            data,
            recipients,
            sign=sign_key_id,
            passphrase=passphrase,
            always_trust=always_trust,
            armor=armor,
        )
        if not result.ok:
            raise CryptoError(f"PGP encrypt failed: {result.status}: {result.stderr}")
        return bytes(result.data)

    def decrypt(
        self, data: bytes, *, passphrase: str | None = None, always_trust: bool | None = None
    ) -> PgpDecryptResult:
        """Decrypt (and, if the payload is also signed, verify).

        Without an explicit passphrase, tries no passphrase first and then every
        distinct ``PGP::Key::Password`` value (Znuny looks up the password of
        the key the message was encrypted for; gpg ≥ 2.5 no longer reports the
        recipients reliably in batch mode, so trying the configured ones is the
        robust equivalent).

        ``verify`` is ``None`` for unsigned payloads; ``ok`` reflects decrypt
        success independently of signature validity.
        """
        gpg = self._gpg()
        if always_trust is None:
            always_trust = self._always_trust
        candidates: list[str | None] = (
            [passphrase]
            if passphrase is not None
            else [None, *dict.fromkeys(self._passwords.values())]
        )
        result: Any = None
        for candidate in candidates:
            result = gpg.decrypt(data, passphrase=candidate, always_trust=always_trust)
            if result.ok:
                break
        assert result is not None
        # A signature inside the payload shows up as a signer fingerprint —
        # also for an unknown signer (ERRSIG), which python-gnupg reports only
        # in ``problems``. Unsigned payloads carry the *encryption* key id in
        # ``key_id`` but no fingerprint.
        verify = _verify_status(result) if result.fingerprint else None
        return PgpDecryptResult(
            ok=bool(result.ok),
            plaintext=bytes(result.data),
            verify=verify,
            status=result.status or "",
        )

    def verify(self, data: bytes) -> PgpVerifyStatus:
        """Verify a clear-signed or inline-signed message (no decryption)."""
        return _verify_status(self._gpg().verify(data))

    def verify_detached(self, data: bytes, signature: bytes) -> PgpVerifyStatus:
        """Verify a detached signature (PGP/MIME ``multipart/signed``, RFC 3156).

        *data* must be the exact signed bytes (the first body part, CRLF
        line endings); gpg reads the signature from a temp file.
        """
        gpg = self._gpg()
        fd, sig_path = tempfile.mkstemp(prefix="tiqora-sig-", suffix=".asc")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(signature)
            return _verify_status(gpg.verify_data(sig_path, data))
        finally:
            with contextlib.suppress(OSError):
                os.unlink(sig_path)


def _verify_status(result: Any) -> PgpVerifyStatus:
    """Normalise a python-gnupg verify/decrypt result.

    ``status`` is python-gnupg's text, except that an unknown signer (gpg
    ``ERRSIG`` + ``NO_PUBKEY``, which python-gnupg only lists in
    ``problems`` after a decrypt) is reported as ``"no public key"``.
    """
    status = str(getattr(result, "status", "") or "")
    problems = [str(p.get("status", "")) for p in getattr(result, "problems", None) or []]
    if not result.valid and "no public key" in problems:
        status = "no public key"
    elif not result.valid and problems and status in ("", "decryption ok"):
        status = problems[0]
    return PgpVerifyStatus(
        valid=bool(result.valid),
        fingerprint=result.fingerprint,
        username=result.username,
        status=status,
        key_id=getattr(result, "key_id", None),
        trust_level=getattr(result, "trust_level", None),
    )
