"""Outbound MIME builder (tiqora.crypto.mime_build): round trips + raw-tool checks.

Every shape is built with Tiqora's support identity (the sender), then

* read back by the inbound walk (:func:`tiqora.crypto.mime_walk.walk_message`)
  with the *customer's* keys — the recipient's view: decrypt + verify, body
  and attachments survive;
* checked with the plain tools (``gpg --verify`` on the extracted signed
  part, ``openssl smime -verify`` / ``-decrypt`` on the raw mail), and for
  RFC 3156 / RFC 8551 conformance: ``protocol`` and ``micalg`` parameters,
  CRLF-only signed part, 7-bit clean content.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Iterator
from email import message_from_bytes, policy
from email.message import EmailMessage
from pathlib import Path

import pytest

from tests._crypto_mail_fixtures import CUSTOMER, SAMPLE_PDF, SUPPORT, CryptoWorld
from tiqora.channels.email.parser import parse_email
from tiqora.channels.email.smtp import build_message
from tiqora.crypto import CryptoError
from tiqora.crypto.config import CryptoConfig, PgpConfig, SmimeConfig
from tiqora.crypto.mime_build import SecurityPlan, secure_message
from tiqora.crypto.mime_walk import _split_entity, _split_multipart, walk_message
from tiqora.crypto.smime_store import SmimeStore

pytestmark = [
    pytest.mark.skipif(shutil.which("gpg") is None, reason="gpg binary not on PATH"),
    pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl binary not on PATH"),
]

BODY = "Grüße aus dem Support.\nZeile mit Leerzeichen am Ende   \n\n-- \nTiqora Support\n"


class Env:
    def __init__(self, world: CryptoWorld) -> None:
        self.world = world
        root = world.root
        # Support side: customer certificate in the store (encrypt target).
        store = SmimeStore(world.cert_dir, world.private_dir)
        self.customer_cert_file = (
            next(
                (e.filename for e in store.list_entries() if e.info and CUSTOMER in e.info.emails),
                None,
            )
            or store.add_certificate(world.customer_cert.cert_pem).filename
        )
        self.support_cert_file = next(
            e.filename
            for e in store.list_entries()
            if e.info and SUPPORT in e.info.emails and e.has_private
        )
        # Customer side: own cert + key, the CA as trust anchor.
        self.c_certs = os.path.join(root, "c_certs")
        self.c_private = os.path.join(root, "c_private")
        cstore = SmimeStore(self.c_certs, self.c_private)
        if not cstore.list_entries():
            cstore.add_certificate(world.customer_cert.cert_pem)
            cstore.add_private_key(world.customer_cert.encrypted_key("c"), "c")
            cstore.add_certificate(world.ca.cert_pem)
        self.ca_file = os.path.join(root, "ca.pem")
        Path(self.ca_file).write_bytes(world.ca.cert_pem)

    def support(self) -> CryptoConfig:
        return self.world.config()

    def customer(self) -> CryptoConfig:
        return CryptoConfig(
            pgp=PgpConfig(enabled=True, homedir=self.world.customer_home),
            smime=SmimeConfig(enabled=True, cert_path=self.c_certs, private_path=self.c_private),
        )


@pytest.fixture(scope="module")
def env() -> Iterator[Env]:
    pytest.importorskip("gnupg")
    w = CryptoWorld.create()
    yield Env(w)
    w.cleanup()


def _msg(*, html: bool = False, attachment: bool = False, bcc: str | None = None) -> EmailMessage:
    msg = build_message(
        from_addr=f"Tiqora Support <{SUPPORT}>",
        to_addrs=f"Carla Customer <{CUSTOMER}>",
        cc_addrs=None,
        bcc_addrs=bcc,
        subject="Re: [Ticket#1] Frage zu Ümläuten",
        body="<p>Grüße <b>aus</b> dem Support</p>" if html else BODY,
        content_type="text/html" if html else "text/plain",
        in_reply_to="<orig@example.com>",
        loop_hint=False,
    )
    if attachment:
        msg.add_attachment(SAMPLE_PDF, maintype="application", subtype="pdf", filename="report.pdf")
    return msg


def _pgp_plan(env: Env, *, sign: bool, encrypt: bool, method: str = "detached") -> SecurityPlan:
    w = env.world
    return SecurityPlan(
        backend="pgp",
        method=method,
        sign_key=w.support_fp if sign else None,
        encrypt_keys=(w.customer_fp,) if encrypt else (),
    )


def _smime_plan(env: Env, *, sign: bool, encrypt: bool) -> SecurityPlan:
    return SecurityPlan(
        backend="smime",
        sign_key=env.support_cert_file if sign else None,
        encrypt_keys=(env.customer_cert_file,) if encrypt else (),
    )


def _top(raw: bytes) -> EmailMessage:
    msg = message_from_bytes(raw, policy=policy.default)
    assert isinstance(msg, EmailMessage)
    return msg


def _assert_wire_clean(raw: bytes) -> None:
    assert b"\n" not in raw.replace(b"\r\n", b"")
    assert all(b < 128 for b in raw), "outbound secured mail must be 7-bit"
    assert b"\r\nBcc:" not in raw and not raw.startswith(b"Bcc:")


def _signed_parts(raw: bytes) -> tuple[bytes, bytes, EmailMessage]:
    """(first part bytes, signature part entity bytes, top header) of multipart/signed."""
    header, body = _split_entity(raw)
    top = _top(header + b"\r\n")
    boundary = top.get_boundary()
    assert boundary
    split = _split_multipart(body, boundary)
    assert split is not None and len(split.parts) == 2
    return split.parts[0], split.parts[1], top


def _gpg_verify(home: str, data: bytes, signature: bytes, tmp: Path) -> str:
    (tmp / "data").write_bytes(data)
    (tmp / "data.asc").write_bytes(signature)
    proc = subprocess.run(  # noqa: S603, S607 — test
        ["gpg", "--homedir", home, "--batch", "--status-fd", "1", "--verify", "data.asc", "data"],
        cwd=tmp,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr.decode()
    return proc.stdout.decode()


def _openssl(args: list[str], data: bytes) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(  # noqa: S603, S607 — test
        ["openssl", *args], input=data, capture_output=True, check=False
    )


# ------------------------------------------------------------------- PGP/MIME


def test_pgp_mime_signed_round_trip_and_gpg_verify(env: Env, tmp_path: Path) -> None:
    out = secure_message(
        _msg(attachment=True), _pgp_plan(env, sign=True, encrypt=False), env.support()
    )
    raw = out.raw
    _assert_wire_clean(raw)
    first, sig_part, top = _signed_parts(raw)
    assert top.get_content_type() == "multipart/signed"
    assert top.get_param("protocol") == "application/pgp-signature"
    assert re.fullmatch(r"pgp-sha(256|384|512|224|1)", str(top.get_param("micalg")))
    # Outer headers stay outside the signed part.
    assert top["Subject"] and top["Message-ID"] and top["In-Reply-To"] == "<orig@example.com>"
    assert b"Subject:" not in first
    # micalg matches the digest gpg actually used.
    status = _gpg_verify(
        env.world.customer_home,
        first,
        message_from_bytes(sig_part).get_payload(decode=True),  # type: ignore[arg-type]
        tmp_path,
    )
    assert "GOODSIG" in status
    algo = re.search(r"VALIDSIG \S+ \S+ \S+ \S+ \S+ \S+ \S+ (\d+)", status)
    assert algo is not None
    assert {"8": "pgp-sha256", "10": "pgp-sha512", "9": "pgp-sha384"}.get(
        algo.group(1), "?"
    ) == str(top.get_param("micalg"))

    walked = walk_message(raw, env.customer())
    assert walked.security is not None
    assert walked.security.status == "verified", walked.security
    assert walked.security.signed and not walked.security.encrypted
    parsed = parse_email(walked.content or b"")
    assert parsed.body == BODY.replace("\n", "\r\n") or parsed.body == BODY
    assert [a.filename for a in parsed.attachments] == ["report.pdf"]
    assert parsed.attachments[0].content == SAMPLE_PDF
    assert out.security.signed and out.security.status == "verified"


def test_pgp_mime_signed_survives_lf_conversion_in_transit(env: Env) -> None:
    out = secure_message(_msg(), _pgp_plan(env, sign=True, encrypt=False), env.support())
    lf_only = out.raw.replace(b"\r\n", b"\n")
    walked = walk_message(lf_only, env.customer())
    assert walked.security is not None and walked.security.status == "verified"


def test_pgp_mime_tampered_body_fails(env: Env) -> None:
    out = secure_message(_msg(), _pgp_plan(env, sign=True, encrypt=False), env.support())
    first, _sig, _top_msg = _signed_parts(out.raw)
    assert b"Tiqora Support\r\n" in first
    # change the signature line inside the signed part
    tampered = out.raw.replace(b"Tiqora Support\r\n", b"Tiqora Hacker!\r\n", 1)
    assert tampered != out.raw
    walked = walk_message(tampered, env.customer())
    assert walked.security is not None and walked.security.status == "verify_failed"


def test_pgp_mime_encrypted_round_trip(env: Env) -> None:
    out = secure_message(
        _msg(attachment=True), _pgp_plan(env, sign=False, encrypt=True), env.support()
    )
    raw = out.raw
    _assert_wire_clean(raw)
    top = _top(raw)
    assert top.get_content_type() == "multipart/encrypted"
    assert top.get_param("protocol") == "application/pgp-encrypted"
    parts = list(top.iter_parts())
    assert parts[0].get_content_type() == "application/pgp-encrypted"
    assert b"Version: 1" in parts[0].get_content()
    assert parts[1].get_content_type() == "application/octet-stream"
    assert b"Gr\xc3\xbc\xc3\x9fe" not in raw and b"%PDF" not in raw
    walked = walk_message(raw, env.customer())
    assert walked.security is not None
    assert walked.security.status == "decrypted" and walked.security.encrypted
    parsed = parse_email(walked.content or b"")
    assert "Grüße aus dem Support." in parsed.body
    assert parsed.attachments[0].content == SAMPLE_PDF


def test_pgp_mime_sign_and_encrypt_round_trip(env: Env) -> None:
    out = secure_message(_msg(), _pgp_plan(env, sign=True, encrypt=True), env.support())
    walked = walk_message(out.raw, env.customer())
    assert walked.security is not None
    assert walked.security.signed and walked.security.encrypted
    assert walked.security.status == "verified", walked.security
    assert "Grüße" in parse_email(walked.content or b"").body
    # the support side cannot read it (encrypted to the customer only)
    assert walk_message(out.raw, env.support()).security.status == "decrypt_failed"  # type: ignore[union-attr]


def test_pgp_encrypt_for_unknown_key_raises(env: Env) -> None:
    plan = SecurityPlan(backend="pgp", encrypt_keys=("0" * 40,))
    with pytest.raises(CryptoError):
        secure_message(_msg(), plan, env.support())


# ------------------------------------------------------------------ PGP inline


def test_pgp_inline_clearsigned(env: Env) -> None:
    out = secure_message(
        _msg(), _pgp_plan(env, sign=True, encrypt=False, method="inline"), env.support()
    )
    top = _top(out.raw)
    assert top.get_content_type() == "text/plain"
    text = top.get_content()
    assert text.startswith("-----BEGIN PGP SIGNED MESSAGE-----")
    walked = walk_message(out.raw, env.customer())
    assert walked.security is not None and walked.security.status == "verified"


def test_pgp_inline_encrypted_with_attachment_and_html_body(env: Env) -> None:
    out = secure_message(
        _msg(html=True, attachment=True),
        _pgp_plan(env, sign=True, encrypt=True, method="inline"),
        env.support(),
    )
    top = _top(out.raw)
    names = [p.get_filename() for p in top.iter_attachments()]
    assert names == ["report.pdf.pgp"]
    assert b"%PDF" not in out.raw
    walked = walk_message(out.raw, env.customer())
    assert walked.security is not None
    assert walked.security.encrypted and walked.security.signed
    parsed = parse_email(walked.content or b"")
    assert "Grüße aus dem Support" in parsed.body and "<b>" not in parsed.body
    assert [a.filename for a in parsed.attachments] == ["report.pdf"]
    assert parsed.attachments[0].content == SAMPLE_PDF


# --------------------------------------------------------------------- S/MIME


def test_smime_signed_round_trip_and_openssl_verify(env: Env) -> None:
    out = secure_message(
        _msg(attachment=True), _smime_plan(env, sign=True, encrypt=False), env.support()
    )
    raw = out.raw
    _assert_wire_clean(raw)
    first, sig_part, top = _signed_parts(raw)
    assert top.get_content_type() == "multipart/signed"
    assert top.get_param("protocol") == "application/pkcs7-signature"
    assert top.get_param("micalg") == "sha-256"
    assert b"\n" not in first.replace(b"\r\n", b"")
    assert b"application/pkcs7-signature" in sig_part
    proc = _openssl(["smime", "-verify", "-CAfile", env.ca_file], raw)
    assert proc.returncode == 0, proc.stderr.decode()
    assert b"Verification successful" in proc.stderr
    walked = walk_message(raw, env.customer())
    assert walked.security is not None and walked.security.status == "verified", walked.security
    parsed = parse_email(walked.content or b"")
    assert "Grüße aus dem Support." in parsed.body
    assert parsed.attachments[0].content == SAMPLE_PDF


def test_smime_encrypted_round_trip_and_openssl_decrypt(env: Env) -> None:
    out = secure_message(_msg(), _smime_plan(env, sign=False, encrypt=True), env.support())
    raw = out.raw
    _assert_wire_clean(raw)
    top = _top(raw)
    assert top.get_content_type() == "application/pkcs7-mime"
    assert top.get_param("smime-type") == "enveloped-data"
    key = os.path.join(env.world.root, "cust.key")
    Path(key).write_bytes(env.world.customer_cert.key_pem)
    proc = _openssl(
        ["smime", "-decrypt", "-recip", env.world.files["customer.crt"], "-inkey", key], raw
    )
    assert proc.returncode == 0, proc.stderr.decode()
    assert b"Content-Type: text/plain" in proc.stdout
    walked = walk_message(raw, env.customer())
    assert walked.security is not None and walked.security.status == "decrypted"
    assert "Grüße" in parse_email(walked.content or b"").body


def test_smime_sign_then_encrypt_round_trip(env: Env) -> None:
    out = secure_message(
        _msg(attachment=True), _smime_plan(env, sign=True, encrypt=True), env.support()
    )
    walked = walk_message(out.raw, env.customer())
    assert walked.security is not None
    assert walked.security.signed and walked.security.encrypted
    assert walked.security.status == "verified", walked.security
    parsed = parse_email(walked.content or b"")
    assert parsed.attachments[0].content == SAMPLE_PDF


def test_bcc_is_kept_for_the_envelope_but_not_on_the_wire(env: Env) -> None:
    out = secure_message(
        _msg(bcc="hidden@example.org"), _smime_plan(env, sign=True, encrypt=False), env.support()
    )
    assert b"hidden@example.org" not in out.raw
    assert out.message["Bcc"] == "hidden@example.org"
    assert out.message.tiqora_raw == out.raw


def test_disabled_backend_raises(env: Env) -> None:
    cfg = CryptoConfig(pgp=PgpConfig(enabled=False), smime=SmimeConfig(enabled=False))
    with pytest.raises(CryptoError):
        secure_message(_msg(), _pgp_plan(env, sign=True, encrypt=False), cfg)
    with pytest.raises(CryptoError):
        secure_message(_msg(), _smime_plan(env, sign=True, encrypt=False), cfg)
