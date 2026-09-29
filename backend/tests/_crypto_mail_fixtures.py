"""Build real PGP/MIME, inline-PGP and S/MIME mails for the inbound crypto tests.

Not a test module (leading underscore). Everything is produced with the real
``gpg`` / ``openssl`` binaries so the tests round-trip through the exact
formats mail clients send:

* :class:`CryptoWorld` — a customer (sender) and the support mailbox
  (recipient): PGP keys (ed25519/cv25519, fast to generate) in two keyrings
  under ``/tmp`` (gpg-agent socket path limit), and an S/MIME CA + leaf
  certificates. ``world.config()`` is the Tiqora side: support's secret key +
  the customer's public key, and a Znuny-layout S/MIME store with support's
  certificate + private key (+ the CA as trust anchor).
* builders for every MIME shape the walker handles.

``python -m tests._crypto_mail_fixtures`` (from ``backend/``) regenerates the
realistic sample mails in ``tests/fixtures/crypto/`` together with the keys
needed to read them (long-lived test certificates, no expiry on PGP keys).
"""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email import policy
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from tests._smime_fixtures import Issued, make_ca, make_leaf
from tiqora.crypto.config import CryptoConfig, PgpConfig, SmimeConfig
from tiqora.crypto.smime_store import SmimeStore

CUSTOMER = "customer@example.com"
SUPPORT = "support@tiqora.test"
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "crypto"


def crlf(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")


def _gpg(home: str) -> Any:
    import gnupg

    g = gnupg.GPG(gnupghome=home)
    g.encoding = "utf-8"
    return g


def gen_pgp_key(home: str, name: str, email: str) -> str:
    g = _gpg(home)
    key = g.gen_key(
        g.gen_key_input(
            name_real=name,
            name_email=email,
            key_type="EDDSA",
            key_curve="ed25519",
            key_usage="sign",
            subkey_type="ECDH",
            subkey_curve="cv25519",
            subkey_usage="encrypt",
            expire_date=0,
            no_protection=True,
        )
    )
    assert key.fingerprint, key.stderr
    return str(key.fingerprint)


@dataclass
class CryptoWorld:
    root: str  # everything lives below this /tmp dir
    customer_home: str
    support_home: str
    customer_fp: str
    support_fp: str
    ca: Issued
    customer_cert: Issued
    support_cert: Issued
    cert_dir: str
    private_dir: str
    files: dict[str, str] = field(default_factory=dict)

    # ------------------------------------------------------------------ setup

    @classmethod
    def create(cls, *, long_lived: bool = False) -> CryptoWorld:
        root = tempfile.mkdtemp(prefix="tq", dir="/tmp")  # noqa: S108 — short socket path
        customer_home = os.path.join(root, "c")
        support_home = os.path.join(root, "s")
        for home in (customer_home, support_home):
            os.mkdir(home, 0o700)
        customer_fp = gen_pgp_key(customer_home, "Carla Customer", CUSTOMER)
        support_fp = gen_pgp_key(support_home, "Tiqora Support", SUPPORT)
        cg, sg = _gpg(customer_home), _gpg(support_home)
        sg.import_keys(cg.export_keys(customer_fp))
        cg.import_keys(sg.export_keys(support_fp))
        now = datetime.now(UTC)
        until = now + timedelta(days=365 * 30 if long_lived else 30)
        ca = make_ca("Example Mail CA", days=365 * 30 if long_lived else 30)
        customer_cert = make_leaf(CUSTOMER, ca=ca, common_name="Carla Customer", not_after=until)
        support_cert = make_leaf(SUPPORT, ca=ca, common_name="Tiqora Support", not_after=until)
        return cls._with_store(
            root,
            customer_home,
            support_home,
            customer_fp,
            support_fp,
            ca,
            customer_cert,
            support_cert,
        )

    @classmethod
    def _with_store(
        cls,
        root: str,
        customer_home: str,
        support_home: str,
        customer_fp: str,
        support_fp: str,
        ca: Issued,
        customer_cert: Issued,
        support_cert: Issued,
        *,
        trust_ca: bool = True,
    ) -> CryptoWorld:
        cert_dir = os.path.join(root, "certs")
        private_dir = os.path.join(root, "private")
        store = SmimeStore(cert_dir, private_dir)
        store.add_certificate(support_cert.cert_pem)
        store.add_private_key(support_cert.encrypted_key("s3cret"), "s3cret")
        if trust_ca:
            store.add_certificate(ca.cert_pem)
        world = cls(
            root,
            customer_home,
            support_home,
            customer_fp,
            support_fp,
            ca,
            customer_cert,
            support_cert,
            cert_dir,
            private_dir,
        )
        for name, data in (
            ("customer.crt", customer_cert.cert_pem),
            ("customer.key", customer_cert.key_pem),
            ("support.crt", support_cert.cert_pem),
        ):
            path = os.path.join(root, name)
            Path(path).write_bytes(data)
            world.files[name] = path
        return world

    def cleanup(self) -> None:
        subprocess.run(  # noqa: S603, S607 — test helper
            ["gpgconf", "--kill", "all"],
            env={**os.environ, "GNUPGHOME": self.support_home},
            check=False,
            capture_output=True,
        )
        shutil.rmtree(self.root, ignore_errors=True)

    def config(self, *, pgp: bool = True, smime: bool = True) -> CryptoConfig:
        return CryptoConfig(
            pgp=PgpConfig(enabled=pgp, homedir=self.support_home),
            smime=SmimeConfig(
                enabled=smime, cert_path=self.cert_dir, private_path=self.private_dir
            ),
        )

    def untrusted_config(self) -> CryptoConfig:
        """Same keys, but the CA certificate is not in CertPath."""
        store = SmimeStore(self.cert_dir, self.private_dir)
        for entry in store.list_entries():
            if entry.info and entry.info.is_ca:
                store.remove_certificate(entry.filename)
        return self.config()

    # ----------------------------------------------------------- PGP builders

    def pgp_detached_sign(self, data: bytes, *, home: str | None = None, fp: str = "") -> bytes:
        g = _gpg(home or self.customer_home)
        sig = g.sign(data, keyid=fp or self.customer_fp, detach=True, binary=False)
        assert sig.data, sig.stderr
        return bytes(sig.data)

    def pgp_encrypt(self, data: bytes, *, sign: bool = False, armor: bool = True) -> bytes:
        g = _gpg(self.customer_home)
        enc = g.encrypt(
            data,
            [self.support_fp],
            sign=self.customer_fp if sign else None,
            always_trust=True,
            armor=armor,
        )
        assert enc.ok, enc.stderr
        return bytes(enc.data)

    def pgp_clearsign(self, text: str) -> str:
        g = _gpg(self.customer_home)
        signed = g.sign(text, keyid=self.customer_fp, clearsign=True)
        assert signed.data, signed.stderr
        return bytes(signed.data).decode("utf-8")

    # --------------------------------------------------------- S/MIME builders

    def _openssl(self, args: list[str], data: bytes) -> bytes:
        proc = subprocess.run(  # noqa: S603, S607 — test helper
            ["openssl", *args], input=data, capture_output=True, check=False
        )
        assert proc.returncode == 0, proc.stderr.decode()
        return proc.stdout

    def smime_detached_signature(self, data: bytes, signer: Issued | None = None) -> bytes:
        """DER PKCS#7 detached signature over the exact bytes of *data*."""
        signer = signer or self.customer_cert
        cert, key = self._pem_files(signer)
        return self._openssl(
            ["smime", "-sign", "-binary", "-signer", cert, "-inkey", key, "-outform", "DER"],
            data,
        )

    def smime_opaque_sign(self, entity: bytes) -> bytes:
        cert, key = self._pem_files(self.customer_cert)
        return crlf(
            self._openssl(
                ["smime", "-sign", "-nodetach", "-binary", "-signer", cert, "-inkey", key],
                entity,
            )
        )

    def smime_encrypt(self, entity: bytes, recipient: Issued | None = None) -> bytes:
        cert, _key = self._pem_files(recipient or self.support_cert)
        return crlf(self._openssl(["smime", "-encrypt", "-aes256", cert], entity))

    def _pem_files(self, issued: Issued) -> tuple[str, str]:
        name = str(abs(hash(issued.cert_pem)))
        cert = os.path.join(self.root, f"{name}.crt")
        key = os.path.join(self.root, f"{name}.key")
        if not os.path.exists(cert):
            Path(cert).write_bytes(issued.cert_pem)
            Path(key).write_bytes(issued.key_pem)
        return cert, key


# ------------------------------------------------------------ MIME builders


def text_entity(text: str, *, subtype: str = "plain") -> bytes:
    msg = EmailMessage()
    msg.set_content(text, subtype=subtype, charset="utf-8")
    del msg["MIME-Version"]
    return msg.as_bytes(policy=policy.SMTP)


def mixed_entity(
    text: str,
    attachments: list[tuple[str, str, bytes]],
    *,
    boundary: str = "mixed-boundary-42",
    protected_subject: str | None = None,
) -> bytes:
    msg = EmailMessage()
    msg.set_content(text, charset="utf-8")
    for filename, ctype, content in attachments:
        maintype, subtype = ctype.split("/", 1)
        msg.add_attachment(content, maintype=maintype, subtype=subtype, filename=filename)
    msg.set_boundary(boundary)
    del msg["MIME-Version"]
    if protected_subject is not None:
        msg.set_param("protected-headers", "v1")
        msg["Subject"] = protected_subject
    return msg.as_bytes(policy=policy.SMTP)


def mail(
    entity: bytes,
    *,
    subject: str = "Crypto test",
    frm: str = f"Carla Customer <{CUSTOMER}>",
    to: str = f"Tiqora Support <{SUPPORT}>",
    extra: str = "",
) -> bytes:
    head = (
        f"From: {frm}\r\nTo: {to}\r\nSubject: {subject}\r\n"
        "Date: Tue, 29 Sep 2026 10:00:00 +0200\r\n"
        f"Message-ID: <{abs(hash(entity))}@example.com>\r\nMIME-Version: 1.0\r\n{extra}"
    )
    return head.encode() + crlf(entity)


def pgp_mime_signed(world: CryptoWorld, inner: bytes, *, boundary: str = "sig-b") -> bytes:
    inner = crlf(inner)
    signature = world.pgp_detached_sign(inner)
    return (
        f'Content-Type: multipart/signed; micalg=pgp-sha512;\r\n protocol="application/'
        f'pgp-signature"; boundary="{boundary}"\r\n\r\n'
        "This is an OpenPGP/MIME signed message (RFC 4880 and 3156)\r\n"
        f"--{boundary}\r\n".encode()
        + inner
        + (
            f"\r\n--{boundary}\r\n"
            'Content-Type: application/pgp-signature; name="OpenPGP_signature.asc"\r\n'
            "Content-Description: OpenPGP digital signature\r\n"
            'Content-Disposition: attachment; filename="OpenPGP_signature.asc"\r\n\r\n'
        ).encode()
        + crlf(signature)
        + f"\r\n--{boundary}--\r\n".encode()
    )


def pgp_mime_encrypted(
    world: CryptoWorld, inner: bytes, *, sign: bool = False, boundary: str = "enc-b"
) -> bytes:
    armored = world.pgp_encrypt(crlf(inner), sign=sign)
    return (
        (
            f'Content-Type: multipart/encrypted;\r\n protocol="application/pgp-encrypted";\r\n'
            f' boundary="{boundary}"\r\n\r\n'
            "This is an OpenPGP/MIME encrypted message (RFC 4880 and 3156)\r\n"
            f"--{boundary}\r\n"
            "Content-Type: application/pgp-encrypted\r\n"
            "Content-Description: PGP/MIME version identification\r\n\r\n"
            "Version: 1\r\n\r\n"
            f"--{boundary}\r\n"
            'Content-Type: application/octet-stream; name="encrypted.asc"\r\n'
            "Content-Description: OpenPGP encrypted message\r\n"
            'Content-Disposition: inline; filename="encrypted.asc"\r\n\r\n'
        ).encode()
        + crlf(armored)
        + f"\r\n--{boundary}--\r\n".encode()
    )


def smime_detached(
    world: CryptoWorld,
    inner: bytes,
    *,
    boundary: str = "----=_NextPart_000_0012_01DB1234.56789ABC",
    signer: Issued | None = None,
    tamper: bool = False,
) -> bytes:
    """Outlook-style ``multipart/signed; protocol="application/x-pkcs7-signature"``."""
    inner = crlf(inner)
    der = world.smime_detached_signature(inner, signer)
    if tamper:
        inner = inner.replace(b"Hello", b"Jello", 1)
    b64 = base64.encodebytes(der).replace(b"\n", b"\r\n")
    return (
        f'Content-Type: multipart/signed;\r\n\tprotocol="application/x-pkcs7-signature";\r\n'
        f'\tmicalg=SHA256;\r\n\tboundary="{boundary}"\r\n\r\n'
        "This is a multipart message in MIME format.\r\n\r\n"
        f"--{boundary}\r\n".encode()
        + inner
        + (
            f"\r\n--{boundary}\r\n"
            'Content-Type: application/x-pkcs7-signature;\r\n\tname="smime.p7s"\r\n'
            "Content-Transfer-Encoding: base64\r\n"
            'Content-Disposition: attachment;\r\n\tfilename="smime.p7s"\r\n\r\n'
        ).encode()
        + b64
        + f"\r\n--{boundary}--\r\n\r\n".encode()
    )


def strip_mime_version(entity: bytes) -> bytes:
    return entity.replace(b"MIME-Version: 1.0\r\n", b"", 1)


# ------------------------------------------------------------ sample mails

SAMPLE_PDF = b"%PDF-1.4\n% Tiqora test attachment\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def thunderbird_pgp_mime(world: CryptoWorld) -> bytes:
    """Thunderbird 115+: sign+encrypt, protected headers, PDF attachment."""
    inner = mixed_entity(
        "Hello support,\r\n\r\nthe invoice is attached, please check it.\r\n\r\nCarla\r\n",
        [("invoice.pdf", "application/pdf", SAMPLE_PDF)],
        boundary="------------tbInnerBoundary0042",
        protected_subject="Invoice 2026-17 question",
    )
    entity = pgp_mime_encrypted(world, inner, sign=True, boundary="------------tbOuterBoundary0042")
    return mail(
        entity,
        subject="...",
        extra="User-Agent: Mozilla Thunderbird\r\nContent-Language: en-US\r\n",
    )


def outlook_smime_signed_entity(world: CryptoWorld) -> bytes:
    """Outlook: detached S/MIME signature over a multipart/alternative body."""
    alt = EmailMessage()
    alt.set_content("Hello team,\r\n\r\nplease reset my VPN token.\r\n\r\nRegards\r\nCarla\r\n")
    alt.add_alternative(
        "<html><body><p>Hello team,</p><p>please reset my VPN token.</p>"
        "<p>Regards<br>Carla</p></body></html>",
        subtype="html",
    )
    alt.set_boundary("----=_NextPart_001_0013_01DB1234.56789ABC")
    del alt["MIME-Version"]
    return smime_detached(world, alt.as_bytes(policy=policy.SMTP))


def outlook_smime_signed(world: CryptoWorld) -> bytes:
    return mail(
        outlook_smime_signed_entity(world),
        subject="VPN token reset",
        extra="X-Mailer: Microsoft Outlook 16.0\r\nThread-Index: AdsSampleThreadIndex==\r\n",
    )


def write_samples(target: Path = FIXTURE_DIR) -> None:
    """Regenerate ``tests/fixtures/crypto/`` (keys + sample mails)."""
    world = CryptoWorld.create(long_lived=True)
    try:
        target.mkdir(parents=True, exist_ok=True)
        cg, sg = _gpg(world.customer_home), _gpg(world.support_home)
        (target / "customer-public.asc").write_text(cg.export_keys(world.customer_fp))
        (target / "support-secret.asc").write_text(
            sg.export_keys(world.support_fp, True, expect_passphrase=False)
        )
        (target / "ca.crt").write_bytes(world.ca.cert_pem)
        (target / "support.crt").write_bytes(world.support_cert.cert_pem)
        (target / "support.key").write_bytes(world.support_cert.key_pem)
        (target / "thunderbird-pgp-mime-signed-encrypted.eml").write_bytes(
            thunderbird_pgp_mime(world)
        )
        (target / "outlook-smime-detached-signed.eml").write_bytes(outlook_smime_signed(world))
        (target / "outlook-smime-encrypted.eml").write_bytes(
            mail(
                world.smime_encrypt(outlook_smime_signed_entity(world)),
                subject="Encrypted reply",
                extra="X-Mailer: Microsoft Outlook 16.0\r\n",
            )
        )
    finally:
        world.cleanup()


if __name__ == "__main__":  # pragma: no cover
    write_samples()
    print(f"wrote samples to {FIXTURE_DIR}")
