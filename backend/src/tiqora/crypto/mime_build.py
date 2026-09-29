"""Outbound: sign and/or encrypt a fully built mail (PGP/MIME, inline PGP, S/MIME).

Port of the crypto part of Znuny's ``Kernel::System::Email::Send`` (the
``EmailSecurity`` block). Runs **after** the normal message (headers, body,
attachments) has been built by :func:`tiqora.channels.email.smtp.
build_message` and before it is handed to the SMTP sender.

Shapes:

* PGP/MIME detached signature — ``multipart/signed; micalg=pgp-<hash>;
  protocol="application/pgp-signature"`` (RFC 3156 §5);
* PGP/MIME encryption — ``multipart/encrypted;
  protocol="application/pgp-encrypted"`` (RFC 3156 §4); sign + encrypt is a
  signed entity inside the encrypted one (RFC 3156 §6.1, as Znuny does);
* PGP inline (``PGP::Inline``) — clear-signed or armored-encrypted text
  body; on encryption the attachments become ``<name>.pgp`` files (the form
  Znuny's ``ArticleCheck`` and our inbound walk decrypt);
* S/MIME detached signature — ``multipart/signed;
  protocol="application/pkcs7-signature"; micalg=sha-256`` (RFC 8551
  §3.5.3); S/MIME encryption — ``application/pkcs7-mime;
  smime-type=enveloped-data``; sign then encrypt nests the signed entity.

The signed/encrypted MIME entity is assembled **as bytes**: everything that
is signed is canonical CRLF, 7-bit clean (text parts quoted-printable,
binary base64 — RFC 3156 §3 / RFC 8551 §3.1), and exactly the bytes that go
on the wire. The result carries the raw message; the SMTP sender sends
those bytes unchanged (re-serialising an ``EmailMessage`` could re-fold a
header or re-encode a part and break the signature).

Keys arrive already resolved (PGP fingerprints, S/MIME ``<hash>.<n>`` store
file names): key choice and validation live in :mod:`tiqora.crypto.compose`.
Everything here is synchronous (gpg/openssl subprocesses) and raises
:class:`~tiqora.crypto.CryptoError` on any failure — the caller must not
fall back to sending in clear.
"""

from __future__ import annotations

import base64
import copy
import secrets
from dataclasses import dataclass, field
from email import message_from_bytes
from email import policy as email_policy
from email.message import EmailMessage, Message

from tiqora.crypto import CryptoError
from tiqora.crypto.config import CryptoConfig
from tiqora.crypto.mime_walk import SecurityResult
from tiqora.crypto.pgp import PgpEngine
from tiqora.crypto.smime import SmimeEngine
from tiqora.crypto.smime_store import SmimeStore

BACKENDS = ("pgp", "smime")
METHODS = ("detached", "inline")

#: Headers that describe the content — they move into the signed/encrypted
#: entity; everything else stays on the outer message.
_CONTENT_HEADERS = frozenset(
    {
        "content-type",
        "content-transfer-encoding",
        "content-disposition",
        "content-description",
        "content-id",
        "content-language",
        "mime-version",
    }
)
_ENTITY_POLICY = email_policy.SMTP  # CRLF, 78-column folding, 7-bit encoded headers


class SecuredEmailMessage(EmailMessage):
    """An :class:`EmailMessage` whose wire form is fixed: ``tiqora_raw``.

    ``tiqora_raw`` never contains ``Bcc``; the header is kept on the object
    only so the SMTP sender can compute the envelope recipients.
    """

    tiqora_raw: bytes = b""


@dataclass(frozen=True)
class SecurityPlan:
    """What to do with one outgoing message (keys already resolved)."""

    backend: str  # "pgp" | "smime"
    method: str = "detached"  # "detached" | "inline" (PGP only)
    #: PGP: fingerprint of the secret key; S/MIME: store file name ``<hash>.<n>``.
    sign_key: str | None = None
    #: PGP: fingerprints; S/MIME: certificate file names.
    encrypt_keys: tuple[str, ...] = ()
    #: S/MIME: signer-relation CA certificates to include (``-certfile``).
    smime_chain: tuple[str, ...] = ()
    #: For the article security flags / badge.
    signer_label: str | None = None

    @property
    def signs(self) -> bool:
        return bool(self.sign_key)

    @property
    def encrypts(self) -> bool:
        return bool(self.encrypt_keys)


@dataclass
class SecuredMessage:
    message: SecuredEmailMessage
    raw: bytes
    security: SecurityResult
    detail: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ helpers


def _boundary(kind: str) -> str:
    return f"----=_tiqora_{kind}_{secrets.token_hex(12)}"


def _crlf(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n").replace(b"\n", b"\r\n")


def _b64_lines(data: bytes) -> bytes:
    enc = base64.b64encode(data)
    return b"\r\n".join(enc[i : i + 76] for i in range(0, len(enc), 76)) + b"\r\n"


def _outer_header_bytes(msg: Message) -> bytes:
    """The message's non-content headers, folded, CRLF, without ``Bcc``."""
    pol = msg.policy.clone(linesep="\r\n")
    out: list[bytes] = []
    for name, value in msg.items():
        low = name.lower()
        if low in _CONTENT_HEADERS or low == "bcc":
            continue
        out.append(pol.fold_binary(name, value))
    return b"".join(out)


def _seven_bit(part: Message) -> None:
    """Re-encode every leaf so the entity is 7-bit clean and whitespace-safe.

    Text becomes quoted-printable (which also protects trailing whitespace —
    the ``"-- "`` signature delimiter — that MTAs like to strip), anything
    else that is not already base64 becomes base64.
    """
    for leaf in list(part.walk()):
        if leaf.is_multipart() or leaf.get_content_maintype() == "message":
            continue
        cte = str(leaf.get("Content-Transfer-Encoding", "7bit")).strip().lower()
        maintype = leaf.get_content_maintype()
        if maintype == "text" and cte == "quoted-printable":
            continue
        if maintype != "text" and cte == "base64":
            continue
        assert isinstance(leaf, EmailMessage)
        filename = leaf.get_filename()
        disposition = leaf.get_content_disposition()
        cid = leaf.get("Content-ID")
        if maintype == "text":
            text = leaf.get_content()
            leaf.set_content(
                text,
                subtype=leaf.get_content_subtype(),
                charset="utf-8",
                cte="quoted-printable",
                disposition=disposition,
                filename=filename,
                cid=cid,
            )
        else:
            payload = leaf.get_payload(decode=True)
            data = payload if isinstance(payload, bytes) else b""
            leaf.set_content(
                data,
                maintype=maintype,
                subtype=leaf.get_content_subtype(),
                cte="base64",
                disposition=disposition,
                filename=filename,
                cid=cid,
            )
        if "MIME-Version" in leaf and leaf is not part:
            del leaf["MIME-Version"]


def content_entity(msg: EmailMessage) -> bytes:
    """The body of *msg* as a standalone MIME entity (content headers only), CRLF."""
    part = copy.deepcopy(msg)
    for name in {k.lower() for k in part.keys()}:  # noqa: SIM118 — Message, not dict
        if name not in _CONTENT_HEADERS or name == "mime-version":
            del part[name]
    if part.get("Content-Type") is None:
        part["Content-Type"] = "text/plain; charset=us-ascii"
    _seven_bit(part)
    if "MIME-Version" in part:
        del part["MIME-Version"]
    return _crlf(part.as_bytes(policy=_ENTITY_POLICY))


def _assemble(outer: bytes, entity: bytes, bcc: str | None) -> tuple[SecuredEmailMessage, bytes]:
    raw = outer + b"MIME-Version: 1.0\r\n" + entity
    parsed = message_from_bytes(raw, _class=SecuredEmailMessage, policy=email_policy.SMTP)
    assert isinstance(parsed, SecuredEmailMessage)
    parsed.tiqora_raw = raw
    if bcc:
        parsed["Bcc"] = bcc
    return parsed, raw


# ------------------------------------------------------------------- PGP/MIME


def pgp_sign_entity(engine: PgpEngine, entity: bytes, key: str) -> bytes:
    signature, micalg = engine.sign_detached(entity, key)
    b = _boundary("sig")
    head = (
        f"Content-Type: multipart/signed; micalg={micalg};\r\n"
        f' protocol="application/pgp-signature";\r\n'
        f' boundary="{b}"\r\n'
    ).encode()
    sig_part = (
        b'Content-Type: application/pgp-signature; name="signature.asc"\r\n'
        b"Content-Description: OpenPGP digital signature\r\n"
        b'Content-Disposition: attachment; filename="signature.asc"\r\n'
        b"\r\n" + _crlf(signature).rstrip(b"\r\n") + b"\r\n"
    )
    delim = f"--{b}".encode()
    body = (
        b"This is an OpenPGP/MIME signed message (RFC 4880 and 3156)\r\n"
        + delim
        + b"\r\n"
        + entity
        + b"\r\n"
        + delim
        + b"\r\n"
        + sig_part
        + b"\r\n"
        + delim
        + b"--\r\n"
    )
    return head + b"\r\n" + body


def pgp_encrypt_entity(engine: PgpEngine, entity: bytes, keys: list[str]) -> bytes:
    armored = engine.encrypt(entity, keys)
    b = _boundary("enc")
    head = (
        f'Content-Type: multipart/encrypted; protocol="application/pgp-encrypted";\r\n'
        f' boundary="{b}"\r\n'
    ).encode()
    delim = f"--{b}".encode()
    body = (
        b"This is an OpenPGP/MIME encrypted message (RFC 4880 and 3156)\r\n"
        + delim
        + b"\r\n"
        + b"Content-Type: application/pgp-encrypted\r\n"
        + b"Content-Description: PGP/MIME version identification\r\n"
        + b"\r\n"
        + b"Version: 1\r\n"
        + b"\r\n"
        + delim
        + b"\r\n"
        + b'Content-Type: application/octet-stream; name="encrypted.asc"\r\n'
        + b"Content-Description: OpenPGP encrypted message\r\n"
        + b'Content-Disposition: inline; filename="encrypted.asc"\r\n'
        + b"\r\n"
        + _crlf(armored).rstrip(b"\r\n")
        + b"\r\n"
        + b"\r\n"
        + delim
        + b"--\r\n"
    )
    return head + b"\r\n" + body


# ------------------------------------------------------------------ PGP inline


def _body_leaf(msg: EmailMessage) -> EmailMessage | None:
    for leaf in msg.walk():
        if leaf.is_multipart():
            continue
        if leaf.get_content_maintype() == "text" and leaf.get_content_disposition() != "attachment":
            assert isinstance(leaf, EmailMessage)
            return leaf
    return None


def _pgp_inline(engine: PgpEngine, msg: EmailMessage, plan: SecurityPlan) -> bytes:
    """Clear-sign / encrypt the text body in place (Znuny ``Method => 'Inline'``)."""
    from tiqora.domain.quoting import html_to_plaintext

    body_leaf = _body_leaf(msg)
    text = ""
    if body_leaf is not None:
        text = body_leaf.get_content()
        if body_leaf.get_content_subtype() == "html":
            # Inline PGP is a plain-text format (Znuny offers it only without
            # rich text); the stored article keeps the HTML.
            text = html_to_plaintext(text)
    text = text.replace("\r\n", "\n")
    if plan.encrypts:
        armored = engine.encrypt(
            text.encode("utf-8"), list(plan.encrypt_keys), sign_key_id=plan.sign_key
        )
        new_text = armored.decode("ascii", "replace")
    elif plan.signs:
        assert plan.sign_key is not None
        new_text = engine.sign(text.encode("utf-8"), plan.sign_key, clearsign=True).decode(
            "utf-8", "replace"
        )
    else:
        new_text = text

    attachments = [
        leaf
        for leaf in msg.walk()
        if not leaf.is_multipart() and leaf is not body_leaf and leaf is not msg
    ]
    out = EmailMessage(policy=_ENTITY_POLICY)
    out.set_content(new_text, subtype="plain", charset="utf-8", cte="quoted-printable")
    for leaf in attachments:
        payload = leaf.get_payload(decode=True)
        data = payload if isinstance(payload, bytes) else b""
        name = leaf.get_filename() or "attachment"
        if plan.encrypts:
            data = engine.encrypt(data, list(plan.encrypt_keys), armor=False)
            out.add_attachment(
                data,
                maintype="application",
                subtype="octet-stream",
                filename=name + ".pgp",
            )
        else:
            out.add_attachment(
                data,
                maintype=leaf.get_content_maintype(),
                subtype=leaf.get_content_subtype(),
                filename=name,
                cid=leaf.get("Content-ID"),
            )
    for sub in out.walk():
        if "MIME-Version" in sub:
            del sub["MIME-Version"]
    return _crlf(out.as_bytes(policy=_ENTITY_POLICY))


# --------------------------------------------------------------------- S/MIME


def _smime_key_paths(store: SmimeStore, filename: str) -> tuple[str, str, str | None]:
    paths = store.private_paths(filename)
    if paths is None:
        raise CryptoError(f"no S/MIME private key for {filename}")
    key_path, secret_path = paths
    secret = secret_path.read_text("utf-8").strip() if secret_path.is_file() else None
    return str(store.cert_dir / filename), str(key_path), secret or None


def smime_sign_entity(
    engine: SmimeEngine,
    store: SmimeStore,
    entity: bytes,
    filename: str,
    chain: list[str],
) -> bytes:
    cert, key, secret = _smime_key_paths(store, filename)
    der = engine.sign_detached_der(entity, cert, key, secret=secret, extra_cert_paths=chain)
    b = _boundary("smime")
    head = (
        'Content-Type: multipart/signed; protocol="application/pkcs7-signature";\r\n'
        f' micalg=sha-256; boundary="{b}"\r\n'
    ).encode()
    delim = f"--{b}".encode()
    sig_part = (
        b'Content-Type: application/pkcs7-signature; name="smime.p7s"\r\n'
        b"Content-Transfer-Encoding: base64\r\n"
        b'Content-Disposition: attachment; filename="smime.p7s"\r\n'
        b"Content-Description: S/MIME Cryptographic Signature\r\n"
        b"\r\n" + _b64_lines(der)
    )
    body = (
        b"This is an S/MIME signed message\r\n"
        + delim
        + b"\r\n"
        + entity
        + b"\r\n"
        + delim
        + b"\r\n"
        + sig_part
        + b"\r\n"
        + delim
        + b"--\r\n"
    )
    return head + b"\r\n" + body


def smime_encrypt_entity(
    engine: SmimeEngine, store: SmimeStore, entity: bytes, filenames: list[str]
) -> bytes:
    certs = [str(store.cert_dir / f) for f in filenames]
    der = engine.encrypt_der(entity, certs)
    return (
        b'Content-Type: application/pkcs7-mime; smime-type=enveloped-data; name="smime.p7m"\r\n'
        b"Content-Transfer-Encoding: base64\r\n"
        b'Content-Disposition: attachment; filename="smime.p7m"\r\n'
        b"Content-Description: S/MIME Encrypted Message\r\n"
        b"\r\n" + _b64_lines(der)
    )


# ------------------------------------------------------------------------ API


def secure_message(msg: EmailMessage, plan: SecurityPlan, config: CryptoConfig) -> SecuredMessage:
    """Sign and/or encrypt *msg*; raises :class:`CryptoError` on any failure."""
    backend = plan.backend.lower()
    method = plan.method.lower()
    if backend not in BACKENDS:
        raise CryptoError(f"unknown email security backend {plan.backend!r}")
    if not (plan.signs or plan.encrypts):
        raise CryptoError("email security needs a sign key and/or encryption keys")
    outer = _outer_header_bytes(msg)
    bcc = msg.get("Bcc")
    detail: list[str] = []

    if backend == "pgp":
        if not config.pgp.enabled:
            raise CryptoError("PGP is not enabled")
        engine = PgpEngine.from_config(config.pgp)
        if method == "inline":
            entity = _pgp_inline(engine, msg, plan)
            detail.append("PGP inline")
        else:
            entity = content_entity(msg)
            if plan.signs:
                assert plan.sign_key is not None
                entity = pgp_sign_entity(engine, entity, plan.sign_key)
            if plan.encrypts:
                entity = pgp_encrypt_entity(engine, entity, list(plan.encrypt_keys))
            detail.append("PGP/MIME")
    else:
        if not config.smime.enabled:
            raise CryptoError("S/MIME is not enabled")
        smime = SmimeEngine(openssl_bin=config.smime.openssl_bin)
        store = SmimeStore.from_config(config.smime)
        entity = content_entity(msg)
        if plan.signs:
            assert plan.sign_key is not None
            entity = smime_sign_entity(smime, store, entity, plan.sign_key, list(plan.smime_chain))
        if plan.encrypts:
            entity = smime_encrypt_entity(smime, store, entity, list(plan.encrypt_keys))
        detail.append("S/MIME")

    if plan.signs:
        detail.append(f"signed with {plan.sign_key}")
    if plan.encrypts:
        n = len(plan.encrypt_keys)
        detail.append(f"encrypted for {n} key{'s' if n != 1 else ''}")
    message, raw = _assemble(outer, entity, str(bcc) if bcc else None)
    security = SecurityResult(
        method=backend,
        signed=plan.signs,
        encrypted=plan.encrypts,
        status="verified" if plan.signs else "decrypted",
        signer=plan.signer_label if plan.signs else None,
        key_id=plan.sign_key if plan.signs else None,
        detail="sent: " + ", ".join(detail),
    )
    return SecuredMessage(message=message, raw=raw, security=security, detail=detail)


__all__ = [
    "BACKENDS",
    "METHODS",
    "SecuredEmailMessage",
    "SecuredMessage",
    "SecurityPlan",
    "content_entity",
    "secure_message",
]
