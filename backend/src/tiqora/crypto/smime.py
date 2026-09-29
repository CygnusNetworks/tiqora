"""S/MIME: sign/verify/encrypt/decrypt via the ``openssl`` CLI.

Mirrors Znuny's ``Kernel::System::Crypt::SMIME``, which shells out to
``openssl smime ...`` for every operation — the ``cryptography`` package
(already a Tiqora dependency, used elsewhere for TOTP/Fernet) has no public
API for S/MIME verify/encrypt/decrypt, only signature *building*
(``cryptography.hazmat.primitives.serialization.pkcs7``), so shelling out to
``openssl`` (same tool Znuny uses) is the pragmatic choice here rather than
hand-rolling PKCS7 parsing.

Certificates and private keys live in Znuny's ``SMIME::CertPath`` /
``SMIME::PrivatePath`` layout (:mod:`tiqora.crypto.smime_store`); private
keys are normally encrypted with the secret stored next to them (``.P``),
which is passed to openssl via an environment variable (never argv).
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass

from tiqora.crypto import CryptoError, CryptoUnavailableError

DEFAULT_OPENSSL_BIN = "openssl"
_TIMEOUT_SECONDS = 30
_SECRET_ENV = "TIQORA_SMIME_SECRET"


@dataclass(frozen=True)
class SmimeVerifyResult:
    valid: bool
    detail: str
    # True only when the signature was checked against a real CA trust root
    # (``ca_path`` given). Without it (``-noverify``) the signature is
    # cryptographically valid but the SIGNER IS UNTRUSTED — a self-signed cert
    # bearing the victim's address also passes (security review M4).
    chain_trusted: bool = False
    #: The signed content (``openssl smime -verify -out``), empty on failure.
    content: bytes = b""
    #: Signer certificate(s) as PEM (``-signer``), when openssl found them.
    signer_pem: bytes = b""


@dataclass(frozen=True)
class SmimeDecryptResult:
    ok: bool
    plaintext: bytes
    detail: str


class SmimeEngine:
    """Thin wrapper around ``openssl smime`` subprocess calls."""

    def __init__(self, *, openssl_bin: str = DEFAULT_OPENSSL_BIN) -> None:
        self._openssl_bin = openssl_bin

    def _run(
        self, args: list[str], input_bytes: bytes, *, secret: str | None = None
    ) -> subprocess.CompletedProcess[bytes]:
        env = {**os.environ, "LC_MESSAGES": "POSIX"}
        if secret is not None:
            env[_SECRET_ENV] = secret
            args = [*args, "-passin", f"env:{_SECRET_ENV}"]
        try:
            return subprocess.run(  # noqa: S603 — fixed binary name, args are our own list
                [self._openssl_bin, *args],
                input=input_bytes,
                capture_output=True,
                check=False,
                timeout=_TIMEOUT_SECONDS,
                env=env,
            )
        except FileNotFoundError as exc:
            raise CryptoUnavailableError(
                f"openssl binary not found ({self._openssl_bin!r}) — S/MIME support requires it"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise CryptoError(f"openssl smime call timed out: {exc}") from exc

    def sign(
        self,
        data: bytes,
        cert_path: str,
        key_path: str,
        *,
        secret: str | None = None,
        extra_cert_paths: list[str] | None = None,
    ) -> bytes:
        """Detached S/MIME signature (``multipart/signed`` MIME output).

        ``extra_cert_paths`` are the signer-relation CA certificates Znuny
        attaches with ``-certfile`` (concatenated into one temp file by the
        caller when there are several — openssl accepts a single file).
        """
        args = ["smime", "-sign", "-signer", cert_path, "-inkey", key_path, "-text"]
        for extra in extra_cert_paths or []:
            args += ["-certfile", extra]
        proc = self._run(args, data, secret=secret)
        if proc.returncode != 0:
            raise CryptoError(
                f"openssl smime -sign failed: {proc.stderr.decode('utf-8', 'replace')}"
            )
        return proc.stdout

    def encrypt(self, data: bytes, recipient_cert_paths: list[str]) -> bytes:
        if not recipient_cert_paths:
            raise CryptoError("openssl smime -encrypt requires at least one recipient cert")
        proc = self._run(["smime", "-encrypt", "-aes256", *recipient_cert_paths], data)
        if proc.returncode != 0:
            raise CryptoError(
                f"openssl smime -encrypt failed: {proc.stderr.decode('utf-8', 'replace')}"
            )
        return proc.stdout

    def decrypt(
        self, data: bytes, cert_path: str, key_path: str, *, secret: str | None = None
    ) -> SmimeDecryptResult:
        proc = self._run(
            ["smime", "-decrypt", "-recip", cert_path, "-inkey", key_path], data, secret=secret
        )
        ok = proc.returncode == 0
        return SmimeDecryptResult(
            ok=ok,
            plaintext=proc.stdout if ok else b"",
            detail=proc.stderr.decode("utf-8", "replace"),
        )

    def verify(
        self,
        data: bytes,
        *,
        ca_path: str | None = None,
        ca_dir: str | None = None,
        no_verify: bool = False,
    ) -> SmimeVerifyResult:
        """Verify a signed S/MIME entity (``multipart/signed`` or opaque signed-data).

        Trust anchors: ``ca_dir`` is used as ``-CApath`` (Znuny passes
        ``SMIME::CertPath``, whose ``<subject_hash>.<n>`` names are exactly the
        ``-CApath`` lookup names), ``ca_path`` as ``-CAfile``; openssl adds its
        default store as Znuny's call does. Without either, or with
        ``no_verify``, ``-noverify`` is used: the signature is checked
        cryptographically but the chain is NOT validated — a self-signed cert
        bearing the victim's address also passes (security review M4), so
        ``chain_trusted`` stays False.

        The result carries the signed content (``-out``) and the signer
        certificate (``-signer``, PEM) when openssl could extract them.
        """
        noverify = no_verify or not (ca_path or ca_dir)
        with tempfile.TemporaryDirectory(prefix="tiqora-smime-") as tmp:
            signer_file = os.path.join(tmp, "signer.pem")
            out_file = os.path.join(tmp, "content")
            args = ["smime", "-verify", "-signer", signer_file, "-out", out_file]
            if noverify:
                args.append("-noverify")
            else:
                if ca_dir:
                    args += ["-CApath", ca_dir]
                if ca_path:
                    args += ["-CAfile", ca_path]
            proc = self._run(args, data)
            content = _read(out_file)
            signer = _read(signer_file)
        ok = proc.returncode == 0
        return SmimeVerifyResult(
            valid=ok,
            detail=proc.stderr.decode("utf-8", "replace"),
            chain_trusted=ok and not noverify,
            content=content if ok else b"",
            signer_pem=signer,
        )


def _read(path: str) -> bytes:
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        return b""
