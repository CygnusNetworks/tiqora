"""S/MIME certificate/private-key store in Znuny's on-disk layout.

Byte-compatible with ``Kernel::System::Crypt::SMIME`` so one directory pair
can be shared with a running Znuny:

* certificates: ``SMIME::CertPath/<subject_hash>.<n>`` (PEM). ``subject_hash``
  is ``openssl x509 -subject_hash`` (the OpenSSL ≥ 1.0 canonical-DN SHA1 hash
  — the same one ``-CApath`` lookups use, which is why Znuny names files this
  way); ``n`` is the first free index 0..99 for colliding hashes.
* private keys: ``SMIME::PrivatePath/<cert filename>`` (PEM, normally
  encrypted) with the passphrase ("secret") in ``<cert filename>.P``.

Znuny additionally indexes both in the ``smime_keys`` table and keeps signer
relations in ``smime_signer_cert_relations``; that DB side lives in
:mod:`tiqora.crypto.smime_index`. This module only touches the filesystem
and is synchronous (``openssl`` subprocesses) — callers run it in a thread.

Differences from Znuny, both deliberate:

* after a delete the remaining ``<hash>.<n>`` files of that hash are
  renumbered without gaps (OpenSSL's ``-CApath`` lookup stops at the first
  missing index, so a gap hides the certificates behind it); the caller gets
  the rename map to update the DB index.
* a private key uploaded *without* a secret is accepted when it is
  unencrypted: it is stored encrypted (AES-256) with a generated secret, so
  the result still satisfies Znuny's "secret required" invariant.
"""

from __future__ import annotations

import os
import re
import secrets
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.x509.oid import NameOID

from tiqora.crypto import CryptoError, CryptoNotFoundError, CryptoUnavailableError

#: ``<8 hex subject hash>.<0-99>`` — also the path-traversal guard for API input.
FILENAME_RE = re.compile(r"^[0-9a-f]{8}\.(?:[0-9]|[1-9][0-9])$")
_HASH_RE = re.compile(r"^[0-9a-f]{8}$")
_MAX_INDEX = 100
_TIMEOUT = 30


class SmimeStoreError(CryptoError):
    """A store operation was refused (duplicate, no matching cert, bad secret…)."""


class SmimeNotFoundError(SmimeStoreError, CryptoNotFoundError):
    """No certificate with that filename."""


@dataclass(frozen=True)
class SmimeCertInfo:
    hash: str
    subject: str  # RFC 4514, for display
    issuer: str
    znuny_subject: str  # the string Znuny writes into smime_keys.subject
    fingerprint: str  # SHA1, "AB:CD:…" (Znuny's format, 59 chars)
    serial: str  # upper-case hex
    not_before: datetime
    not_after: datetime
    emails: list[str]
    is_ca: bool
    public_key_der: bytes = field(repr=False)

    @property
    def email_joined(self) -> str:
        """Znuny's ``Email`` attribute: sorted, comma-joined."""
        return ", ".join(sorted(self.emails))

    @property
    def short_end_date(self) -> date:
        return self.not_after.date()

    def is_expired(self, now: datetime | None = None) -> bool:
        return self.not_after <= (now or datetime.now(UTC))


@dataclass(frozen=True)
class SmimeEntry:
    filename: str
    info: SmimeCertInfo | None  # None: unreadable/invalid file (Znuny shows "Invalid")
    has_private: bool


def validate_filename(filename: str) -> str:
    if not FILENAME_RE.match(filename or ""):
        raise SmimeStoreError(f"invalid S/MIME filename {filename!r} (expected <hash>.<n>)")
    return filename


def load_certificate(data: bytes) -> x509.Certificate:
    """Parse PEM or DER certificate bytes."""
    stripped = data.strip()
    try:
        if b"-----BEGIN" in stripped:
            return x509.load_pem_x509_certificate(stripped)
        return x509.load_der_x509_certificate(stripped)
    except ValueError as exc:
        raise SmimeStoreError(f"not a valid X.509 certificate: {exc}") from exc


def _emails(cert: x509.Certificate) -> list[str]:
    found: list[str] = []
    for attr in cert.subject.get_attributes_for_oid(NameOID.EMAIL_ADDRESS):
        found.append(str(attr.value))
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        found.extend(str(v) for v in san.value.get_values_for_type(x509.RFC822Name))
    except x509.ExtensionNotFound:
        pass
    out: list[str] = []
    for e in found:
        low = e.strip().lower()
        if low and low not in out:
            out.append(low)
    return out


def _is_ca(cert: x509.Certificate) -> bool:
    try:
        bc = cert.extensions.get_extension_for_class(x509.BasicConstraints)
    except x509.ExtensionNotFound:
        return False
    return bool(bc.value.ca)


def _display_dn(name: x509.Name) -> str:
    """Most-specific-last DN as openssl prints it (``CN=x, emailAddress=y``)."""
    parts = name.rfc4514_string({NameOID.EMAIL_ADDRESS: "emailAddress"}).split(",")
    # rfc4514 reverses the RDN order; escape-aware enough for display purposes.
    return ", ".join(reversed(parts))


def _znuny_dn(raw: str) -> str:
    """Znuny's post-processing of ``openssl x509 -subject`` output."""
    value = raw.strip()
    if value.startswith("/"):
        value = value[1:]
    return value.replace("/", " ").replace("=", "= ")


class SmimeStore:
    """Filesystem half of the Znuny-compatible S/MIME key store."""

    def __init__(self, cert_path: str, private_path: str, *, openssl_bin: str = "openssl") -> None:
        if not cert_path:
            raise CryptoUnavailableError(
                "S/MIME certificate directory (SMIME::CertPath) is not configured"
            )
        self.cert_dir = Path(cert_path)
        self.private_dir = Path(private_path) if private_path else None
        self._openssl = openssl_bin or "openssl"

    @classmethod
    def from_config(cls, cfg: object) -> SmimeStore:
        """Build from a :class:`tiqora.crypto.config.SmimeConfig`."""
        return cls(
            str(getattr(cfg, "cert_path", "")),
            str(getattr(cfg, "private_path", "")),
            openssl_bin=str(getattr(cfg, "openssl_bin", "openssl")),
        )

    # ------------------------------------------------------------ openssl

    def _run(
        self, args: list[str], data: bytes, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[bytes]:
        try:
            return subprocess.run(  # noqa: S603 — fixed binary, our own arg list
                [self._openssl, *args],
                input=data,
                capture_output=True,
                check=False,
                timeout=_TIMEOUT,
                env={**os.environ, "LC_MESSAGES": "POSIX", **(env or {})},
            )
        except FileNotFoundError as exc:
            raise CryptoUnavailableError(f"openssl binary not found ({self._openssl!r})") from exc
        except subprocess.TimeoutExpired as exc:
            raise CryptoError(f"openssl timed out: {exc}") from exc

    def cert_attributes(self, data: bytes) -> SmimeCertInfo:
        cert = load_certificate(data)
        pem = cert.public_bytes(serialization.Encoding.PEM)
        proc = self._run(["x509", "-noout", "-subject_hash", "-subject", "-issuer"], pem)
        if proc.returncode != 0:
            raise SmimeStoreError(
                f"openssl x509 failed: {proc.stderr.decode('utf-8', 'replace').strip()}"
            )
        cert_hash = ""
        subject_raw = ""
        for line in proc.stdout.decode("utf-8", "replace").splitlines():
            line = line.strip()
            if not cert_hash and _HASH_RE.match(line):
                cert_hash = line
            elif line.lower().startswith("subject="):
                subject_raw = line.split("=", 1)[1]
        if not cert_hash:
            raise SmimeStoreError("openssl did not return a subject hash")
        fp = cert.fingerprint(hashes.SHA1()).hex().upper()  # noqa: S303 — Znuny's key identity
        return SmimeCertInfo(
            hash=cert_hash,
            subject=_display_dn(cert.subject),
            issuer=_display_dn(cert.issuer),
            znuny_subject=_znuny_dn(subject_raw),
            fingerprint=":".join(fp[i : i + 2] for i in range(0, len(fp), 2)),
            serial=format(cert.serial_number, "X"),
            not_before=cert.not_valid_before_utc,
            not_after=cert.not_valid_after_utc,
            emails=_emails(cert),
            is_ca=_is_ca(cert),
            public_key_der=cert.public_key().public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
            ),
        )

    def _private_public_der(self, key: bytes, secret: str) -> bytes:
        proc = self._run(
            ["pkey", "-pubout", "-outform", "DER", "-passin", "env:TIQORA_SMIME_SECRET"],
            key,
            env={"TIQORA_SMIME_SECRET": secret},
        )
        if proc.returncode != 0 or not proc.stdout:
            raise SmimeStoreError("cannot read private key (wrong secret or not a private key)")
        return proc.stdout

    def _key_is_encrypted(self, key: bytes) -> bool:
        proc = self._run(
            ["pkey", "-noout", "-passin", "pass:"],
            key,
        )
        return proc.returncode != 0

    def _encrypt_key(self, key: bytes, secret: str) -> bytes:
        proc = self._run(
            ["pkey", "-aes256", "-passout", "env:TIQORA_SMIME_SECRET"],
            key,
            env={"TIQORA_SMIME_SECRET": secret},
        )
        if proc.returncode != 0 or b"ENCRYPTED" not in proc.stdout:
            raise SmimeStoreError("could not protect the private key with a secret")
        return proc.stdout

    # ------------------------------------------------------------ reading

    def _cert_file(self, filename: str) -> Path:
        return self.cert_dir / validate_filename(filename)

    def _private_file(self, filename: str) -> Path | None:
        if self.private_dir is None:
            return None
        return self.private_dir / validate_filename(filename)

    def _cert_filenames(self) -> list[str]:
        if not self.cert_dir.is_dir():
            return []
        return sorted(p.name for p in self.cert_dir.iterdir() if FILENAME_RE.match(p.name))

    def entry(self, filename: str) -> SmimeEntry:
        path = self._cert_file(filename)
        if not path.is_file():
            raise SmimeNotFoundError(f"certificate {filename} not found")
        try:
            info: SmimeCertInfo | None = self.cert_attributes(path.read_bytes())
        except CryptoUnavailableError:
            raise
        except CryptoError:
            info = None
        priv = self._private_file(filename)
        return SmimeEntry(filename=filename, info=info, has_private=bool(priv and priv.is_file()))

    def list_entries(self) -> list[SmimeEntry]:
        return [self.entry(name) for name in self._cert_filenames()]

    def get_certificate(self, filename: str) -> bytes:
        path = self._cert_file(filename)
        if not path.is_file():
            raise SmimeNotFoundError(f"certificate {filename} not found")
        return path.read_bytes()

    def get_private(self, filename: str) -> tuple[bytes, str] | None:
        """``(key_pem, secret)`` for *filename*, or ``None`` when there is no key."""
        path = self._private_file(filename)
        if path is None or not path.is_file():
            return None
        secret_file = path.with_name(path.name + ".P")
        secret = secret_file.read_text("utf-8") if secret_file.is_file() else ""
        return path.read_bytes(), secret

    def private_paths(self, filename: str) -> tuple[Path, Path] | None:
        path = self._private_file(filename)
        if path is None or not path.is_file():
            return None
        return path, path.with_name(path.name + ".P")

    def find_by_fingerprint(self, fingerprint: str) -> SmimeEntry | None:
        want = fingerprint.replace(":", "").upper()
        for entry in self.list_entries():
            if entry.info and entry.info.fingerprint.replace(":", "") == want:
                return entry
        return None

    def search(
        self, email: str, *, private: bool = False, valid_only: bool = False
    ) -> list[SmimeEntry]:
        needle = email.strip().lower()
        now = datetime.now(UTC)
        out = []
        for entry in self.list_entries():
            if entry.info is None or needle not in entry.info.emails:
                continue
            if private and not entry.has_private:
                continue
            if valid_only and entry.info.is_expired(now):
                continue
            out.append(entry)
        return out

    # ------------------------------------------------------------ writing

    def add_certificate(self, data: bytes) -> SmimeEntry:
        """Store a certificate as ``<hash>.<first free n>`` (Znuny ``CertificateAdd``)."""
        info = self.cert_attributes(data)
        pem = load_certificate(data).public_bytes(serialization.Encoding.PEM)
        self.cert_dir.mkdir(parents=True, exist_ok=True)
        for name in self._cert_filenames():
            if not name.startswith(info.hash + "."):
                continue
            existing = self.entry(name)
            if existing.info and existing.info.fingerprint == info.fingerprint:
                raise SmimeStoreError(f"certificate already installed as {name}")
        for n in range(_MAX_INDEX):
            path = self.cert_dir / f"{info.hash}.{n}"
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            except FileExistsError:
                continue
            try:
                os.write(fd, pem)
            finally:
                os.close(fd)
            return SmimeEntry(filename=path.name, info=info, has_private=False)
        raise SmimeStoreError(f"no more available filenames for certificate hash {info.hash}")

    def add_private_key(self, key: bytes, secret: str) -> tuple[SmimeEntry, bool]:
        """Store a private key next to its certificate (Znuny ``PrivateAdd``).

        Returns ``(entry, secret_generated)``. The key must match exactly one
        stored certificate by public key.
        """
        if self.private_dir is None:
            raise CryptoUnavailableError(
                "S/MIME private key directory (SMIME::PrivatePath) is not configured"
            )
        generated = False
        if not secret:
            if self._key_is_encrypted(key):
                raise SmimeStoreError("the private key is encrypted — its secret is required")
            secret = secrets.token_urlsafe(24)
            key = self._encrypt_key(key, secret)
            generated = True
        public = self._private_public_der(key, secret)
        matches = [
            e for e in self.list_entries() if e.info is not None and e.info.public_key_der == public
        ]
        if not matches:
            raise SmimeStoreError(
                "no certificate for this private key — upload the certificate first"
            )
        if len(matches) > 1:
            raise SmimeStoreError(
                "multiple certificates share this key ("
                + ", ".join(m.filename for m in matches)
                + ") — cannot assign the private key"
            )
        target = matches[0]
        if not self.private_dir.exists():
            self.private_dir.mkdir(parents=True, exist_ok=True)
            self.private_dir.chmod(0o700)
        path = self.private_dir / target.filename
        _write_private(path, key)
        _write_private(path.with_name(path.name + ".P"), secret.encode("utf-8"))
        return SmimeEntry(filename=target.filename, info=target.info, has_private=True), generated

    def remove_private(self, filename: str) -> bool:
        """Delete the private key + secret. Returns False when there was none."""
        path = self._private_file(filename)
        if path is None or not path.is_file():
            return False
        path.with_name(path.name + ".P").unlink(missing_ok=True)
        path.unlink()
        return True

    def remove_certificate(self, filename: str) -> dict[str, str]:
        """Delete a certificate (and its private key); compact the hash's indices.

        Returns the ``old → new`` filename renames done by the compaction.
        """
        path = self._cert_file(filename)
        if not path.is_file():
            raise SmimeNotFoundError(f"certificate {filename} not found")
        self.remove_private(filename)
        path.unlink()
        return self._compact(filename.split(".", 1)[0])

    def _move(self, old: str, new: str) -> None:
        (self.cert_dir / old).rename(self.cert_dir / new)
        if self.private_dir is not None:
            priv = self.private_dir / old
            if priv.is_file():
                priv.rename(self.private_dir / new)
            sec = self.private_dir / f"{old}.P"
            if sec.is_file():
                sec.rename(self.private_dir / f"{new}.P")

    def _compact(self, cert_hash: str) -> dict[str, str]:
        names = [n for n in self._cert_filenames() if n.startswith(cert_hash + ".")]
        indices = sorted(int(n.split(".", 1)[1]) for n in names)
        renames: dict[str, str] = {}
        for target, current in enumerate(indices):
            if target == current:
                continue
            old, new = f"{cert_hash}.{current}", f"{cert_hash}.{target}"
            self._move(old, new)
            renames[old] = new
        return renames

    def rehash(self) -> dict[str, str]:
        """Rename files whose hash prefix no longer matches ``openssl -subject_hash``.

        Znuny's ``_ReHashCertificates`` (needed after an OpenSSL hash algorithm
        change, or for files copied in by hand). Also compacts index gaps.
        Returns ``old → new`` renames.
        """
        renames: dict[str, str] = {}
        for name in self._cert_filenames():
            entry = self.entry(name)
            if entry.info is None or name.startswith(entry.info.hash + "."):
                continue
            for n in range(_MAX_INDEX):
                new = f"{entry.info.hash}.{n}"
                if not (self.cert_dir / new).exists():
                    self._move(name, new)
                    renames[name] = new
                    break
        hashes_seen = {n.split(".", 1)[0] for n in self._cert_filenames()}
        for h in sorted(hashes_seen):
            for old, new in self._compact(h).items():
                origin = next((k for k, v in renames.items() if v == old), old)
                renames[origin] = new
        return {k: v for k, v in renames.items() if k != v}


def _write_private(path: Path, data: bytes) -> None:
    """Write with 0600 from the start — no world-readable window (review M4)."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    path.chmod(0o600)


__all__ = [
    "FILENAME_RE",
    "SmimeCertInfo",
    "SmimeEntry",
    "SmimeNotFoundError",
    "SmimeStore",
    "SmimeStoreError",
    "load_certificate",
    "validate_filename",
]
