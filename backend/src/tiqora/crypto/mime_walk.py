"""Inbound MIME-tree walk: decrypt and verify PGP / S/MIME parts of a mail.

Port of what Znuny's ``Kernel::Output::HTML::ArticleCheck::PGP`` and
``::SMIME`` do on article view, run once at ingest (and on view for legacy
Znuny articles, see :mod:`tiqora.crypto.article_view`):

* PGP/MIME ``multipart/encrypted`` (RFC 3156): the second part is decrypted
  and the resulting entity replaces the node, so body **and attachments** of
  the encrypted mail survive;
* PGP/MIME ``multipart/signed``: the detached signature is checked over the
  *exact raw bytes* of the first part (CRLF canonical form) — the tree is
  split on the raw bytes, never re-serialised before verifying;
* inline PGP in ``text/plain`` parts (``BEGIN PGP MESSAGE`` /
  ``BEGIN PGP SIGNED MESSAGE``) and PGP-encrypted attachments
  (``*.pgp``/``*.gpg``/``*.asc`` with an armored message — Znuny strips the
  extension the same way);
* S/MIME ``multipart/signed`` with ``application/(x-)pkcs7-signature`` and
  opaque ``application/(x-)pkcs7-mime; smime-type=signed-data``;
* S/MIME ``enveloped-data``, decrypted with every private key whose
  certificate carries one of the recipient addresses (``To``, ``Cc``,
  ``Delivered-To``, ``Resent-To``, ``Envelope-To``, ``X-Original-To``) until
  one works — Znuny's ``PrivateSearch`` loop;
* anything nested (signed inside encrypted, encrypted attachment inside a
  multipart/mixed) through recursion.

Trust:

* S/MIME chains are validated with ``SMIME::CertPath`` as ``-CApath`` (plus
  the optional ``TIQORA_CRYPTO_SMIME_CA_PATH`` bundle, the bundled Mozilla
  e-mail roots unless ``smime.public_roots`` is off, and openssl's default
  store, like Znuny's call). Signer-relation CAs live in CertPath, so they are
  trust anchors automatically. A signature that is cryptographically fine but
  whose chain does not validate is ``signed_untrusted`` (Znuny's ``-noverify``
  retry), or ``verified`` when ``SMIME::NoVerify`` is on (Znuny semantics).
* PGP: a good signature from a key in the keyring is ``verified`` (Znuny's
  ``GOODSIG`` rule); an expired/revoked key or ownertrust *never* makes it
  ``signed_untrusted``; a key missing from the keyring is ``unknown_key``.
* RFC 3850 §3 / Znuny bug#5098: when the signer's addresses do not include
  the ``From``/``Sender`` address, a verified signature is downgraded to
  ``signed_untrusted`` (applied to both backends).

Everything here is synchronous (gpg/openssl subprocesses) and never raises
for a crypto problem: failures become layer results and the node is left
unchanged, so delivery is never blocked.
"""

from __future__ import annotations

import mimetypes
import re
from dataclasses import dataclass, field
from email import message_from_bytes, policy
from email.message import EmailMessage, Message
from email.parser import BytesHeaderParser
from email.utils import getaddresses
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.x509.oid import NameOID

from tiqora.crypto import CryptoError, CryptoUnavailableError
from tiqora.crypto.config import CryptoConfig
from tiqora.crypto.pgp import PgpEngine, PgpVerifyStatus
from tiqora.crypto.smime import PUBLIC_ROOTS_FILE, SmimeEngine
from tiqora.crypto.smime_store import SmimeStore, load_certificate
from tiqora.crypto.smime_store import _emails as cert_emails

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z0-9-]{2,63}")
_PGP_MESSAGE_RE = re.compile(
    r"-----BEGIN PGP MESSAGE-----.*?-----END PGP MESSAGE-----[^\n]*", re.DOTALL
)
_PGP_SIGNED_RE = re.compile(
    r"-----BEGIN PGP SIGNED MESSAGE-----\r?\n(.*?)\r?\n\r?\n(.*?)\r?\n"
    r"-----BEGIN PGP SIGNATURE-----.*?-----END PGP SIGNATURE-----[^\n]*",
    re.DOTALL,
)
_PGP_ARMOR_MESSAGE = b"-----BEGIN PGP MESSAGE-----"
_ENCRYPTED_EXT_RE = re.compile(r"(\.[^.]+)\.(?:pgp|gpg|asc)$", re.IGNORECASE)
_RECIPIENT_HEADERS = ("Resent-To", "Envelope-To", "To", "Cc", "Delivered-To", "X-Original-To")

#: Worst first — the combined status of a message is its worst layer.
_STATUS_RANK = (
    "decrypt_failed",
    "verify_failed",
    "error",
    "unavailable",
    "unknown_key",
    "signed_untrusted",
    "verified",
    "decrypted",
)

#: Only statuses that mean "the signature proved something bad/unknown" are
#: kept when nothing better is available; used by the API/UI tone mapping.
SECURITY_STATUSES = frozenset(_STATUS_RANK)

_MAX_DEPTH = 12


# ---------------------------------------------------------------- results


@dataclass(frozen=True)
class LayerResult:
    """One crypto layer found in the tree (a signature or an encryption)."""

    method: str  # "pgp" | "smime"
    kind: str  # "signature" | "encryption"
    status: str  # see _STATUS_RANK
    signer: str | None = None
    key_id: str | None = None
    detail: str = ""
    signer_emails: tuple[str, ...] = ()


@dataclass(frozen=True)
class SecurityResult:
    """Article security summary (the API's ``security`` object)."""

    method: str
    signed: bool
    encrypted: bool
    status: str
    signer: str | None = None
    key_id: str | None = None
    detail: str = ""

    @property
    def article_flag_value(self) -> str:
        """``TiqoraCryptoVerify`` value — ``<method>:<status>`` (pre-B2 format)."""
        return f"{self.method}:{self.status}"


def combine_layers(layers: list[LayerResult]) -> SecurityResult | None:
    if not layers:
        return None
    worst = min(
        layers,
        key=lambda la: _STATUS_RANK.index(la.status) if la.status in _STATUS_RANK else 2,
    )
    signatures = [la for la in layers if la.kind == "signature"]
    encryptions = [la for la in layers if la.kind == "encryption"]
    ref = worst if worst.kind == "signature" or not signatures else signatures[0]
    key_id = ref.key_id or next((la.key_id for la in layers if la.key_id), None)
    return SecurityResult(
        method=layers[0].method,
        signed=bool(signatures),
        encrypted=bool(encryptions),
        status=worst.status,
        signer=ref.signer,
        key_id=key_id,
        detail=worst.detail or ref.detail,
    )


@dataclass
class WalkOutcome:
    """Result of :func:`walk_message`.

    ``content`` is the rewritten MIME entity (decrypted / signature parts
    removed) to take body and attachments from, or ``None`` when nothing was
    replaced. It carries only the entity headers of the innermost content,
    not the outer mail headers — subject, addresses and routing still come
    from the original message. ``protected_subject`` is the ``Subject`` of a
    decrypted entity sent with protected headers (Thunderbird/Enigmail
    ``protected-headers="v1"``).
    """

    security: SecurityResult | None
    content: bytes | None = None
    layers: list[LayerResult] = field(default_factory=list)
    protected_subject: str | None = None


# ---------------------------------------------------------------- raw MIME


def _crlf(data: bytes) -> bytes:
    return re.sub(rb"\r?\n", b"\r\n", data)


def _split_entity(raw: bytes) -> tuple[bytes, bytes]:
    """``(header_block, body)`` of a CRLF entity; the blank line is dropped."""
    if raw.startswith(b"\r\n"):
        return b"", raw[2:]
    idx = raw.find(b"\r\n\r\n")
    if idx < 0:
        return raw, b""
    return raw[: idx + 2], raw[idx + 4 :]


def _headers(header_block: bytes) -> Message:
    return BytesHeaderParser(policy=policy.compat32).parsebytes(header_block + b"\r\n")


@dataclass
class _Multipart:
    delimiters: list[bytes]  # every delimiter line incl. its leading/trailing CRLF
    parts: list[bytes]
    preamble: bytes
    epilogue: bytes

    def join(self, parts: list[bytes]) -> bytes:
        out = [self.preamble]
        for delim, part in zip(self.delimiters, parts, strict=False):
            out.append(delim)
            out.append(part)
        out.append(self.delimiters[-1])
        out.append(self.epilogue)
        return b"".join(out)


def _split_multipart(body: bytes, boundary: str) -> _Multipart | None:
    """Split a multipart body on the raw bytes (RFC 2046 §5.1.1).

    The CRLF before a delimiter belongs to the delimiter, so each part is
    exactly the byte range RFC 3156 / RFC 5751 sign.
    """
    pattern = re.compile(
        rb"(?:\A|\r\n)--"
        + re.escape(boundary.encode("utf-8", "replace"))
        + rb"(--)?[ \t]*(?:\r\n|\Z)"
    )
    matches = list(pattern.finditer(body))
    if len(matches) < 2:
        return None
    close = next((i for i, m in enumerate(matches) if m.group(1)), None)
    if close is None or close == 0:
        return None
    matches = matches[: close + 1]
    parts = [body[matches[i].end() : matches[i + 1].start()] for i in range(len(matches) - 1)]
    return _Multipart(
        delimiters=[m.group(0) for m in matches],
        parts=parts,
        preamble=body[: matches[0].start()],
        epilogue=body[matches[-1].end() :],
    )


def _param(msg: Message, name: str) -> str:
    value = msg.get_param(name, header="content-type")
    if isinstance(value, tuple):
        value = value[2]
    return str(value or "").strip().lower() if name != "boundary" else str(value or "")


def _as_entity(msg: EmailMessage) -> bytes:
    return msg.as_bytes(policy=policy.SMTP)


def _text_entity(text: str) -> bytes:
    msg = EmailMessage()
    msg.set_content(text, subtype="plain", charset="utf-8", cte="base64")
    del msg["MIME-Version"]
    return _as_entity(msg)


def _attachment_entity(content: bytes, filename: str) -> bytes:
    ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    maintype, subtype = ctype.split("/", 1)
    msg = EmailMessage()
    msg.set_content(content, maintype=maintype, subtype=subtype, filename=filename)
    del msg["MIME-Version"]
    return _as_entity(msg)


def _decode_text(part: Message) -> str:
    payload = part.get_payload(decode=True)
    data = payload if isinstance(payload, bytes) else b""
    charset = part.get_content_charset() or "utf-8"
    try:
        return data.decode(charset, "replace")
    except LookupError:
        return data.decode("utf-8", "replace")


def _decode_plain(data: bytes, charset: str) -> str:
    try:
        return data.decode(charset or "utf-8")
    except (LookupError, UnicodeDecodeError):
        return data.decode("utf-8", "replace")


def _emails(value: str) -> list[str]:
    return [m.lower() for m in _EMAIL_RE.findall(value or "")]


# ------------------------------------------------------------------ walker


class _Walker:
    def __init__(self, config: CryptoConfig, outer: Message) -> None:
        self.config = config
        self.layers: list[LayerResult] = []
        self.protected_subject: str | None = None
        recipients: list[str] = []
        for header in _RECIPIENT_HEADERS:
            for _name, addr in getaddresses([str(v) for v in outer.get_all(header, [])]):
                low = addr.strip().lower()
                if low and "@" in low and low not in recipients:
                    recipients.append(low)
        self.recipients = recipients
        senders: list[str] = []
        for header in ("From", "Sender"):
            for _name, addr in getaddresses([str(v) for v in outer.get_all(header, [])]):
                if addr and "@" in addr:
                    senders.append(addr.strip().lower())
        self.senders = senders
        self._pgp: PgpEngine | None = None
        self._pgp_error: str | None = None

    # -- engines

    def pgp(self) -> PgpEngine | None:
        if self._pgp is None and self._pgp_error is None:
            try:
                self._pgp = PgpEngine.from_config(self.config.pgp)
            except CryptoUnavailableError as exc:
                self._pgp_error = str(exc)
        return self._pgp

    def smime(self) -> SmimeEngine:
        return SmimeEngine(openssl_bin=self.config.smime.openssl_bin)

    def _unavailable(self, method: str, kind: str, detail: str) -> None:
        self.layers.append(LayerResult(method, kind, "unavailable", detail=detail))

    # -- sender check

    def _check_sender(self, layer: LayerResult) -> LayerResult:
        if layer.status != "verified" or not layer.signer_emails or not self.senders:
            return layer
        if any(s in layer.signer_emails for s in self.senders):
            return layer
        return LayerResult(
            layer.method,
            layer.kind,
            "signed_untrusted",
            signer=layer.signer,
            key_id=layer.key_id,
            detail=(
                f"signed by {', '.join(layer.signer_emails)}, which does not match "
                f"the sender {', '.join(self.senders)}"
            ),
            signer_emails=layer.signer_emails,
        )

    # -- PGP signature status

    def _pgp_signature_layer(self, verify: PgpVerifyStatus) -> LayerResult:
        status_text = (verify.status or "").lower()
        emails: tuple[str, ...] = ()
        key = None
        engine = self.pgp()
        if verify.fingerprint and engine is not None:
            try:
                key = engine.find_key(verify.fingerprint)
            except CryptoError:
                key = None
        if key is not None:
            emails = tuple(key.emails)
        elif verify.username:
            emails = tuple(_emails(verify.username))
        signer = verify.username or (key.uids[0] if key and key.uids else None)
        key_id = verify.fingerprint or verify.key_id
        if verify.valid:
            if key is not None and key.status != "good":
                status, detail = "signed_untrusted", f"signing key is {key.status}"
            elif verify.trust_level == 2:  # TRUST_NEVER
                status, detail = "signed_untrusted", "signing key has ownertrust 'never'"
            else:
                status, detail = "verified", "good signature"
        elif "no public key" in status_text:
            status, detail = "unknown_key", "signing key is not in the keyring"
            signer = signer or None
        elif "expired" in status_text or "revoked" in status_text:
            status, detail = "signed_untrusted", verify.status
        else:
            status, detail = "verify_failed", verify.status or "bad signature"
        return self._check_sender(
            LayerResult("pgp", "signature", status, signer, key_id, detail, emails)
        )

    # -- S/MIME signature status

    def _smime_verify(self, entity: bytes) -> tuple[LayerResult, bytes]:
        cfg = self.config.smime
        engine = self.smime()
        ca_dir = cfg.cert_path or None
        ca_file = cfg.ca_path or None
        extra = (PUBLIC_ROOTS_FILE,) if cfg.public_roots else ()
        trusted = engine.verify(entity, ca_dir=ca_dir, ca_path=ca_file, extra_ca_files=extra)
        result = trusted
        if not trusted.valid:
            result = engine.verify(entity, no_verify=True)
        signer: str | None = None
        key_id: str | None = None
        emails: tuple[str, ...] = ()
        if result.signer_pem:
            first = result.signer_pem.split(b"-----END CERTIFICATE-----")[0]
            try:
                cert = load_certificate(first + b"-----END CERTIFICATE-----\n")
            except CryptoError:
                cert = None
            if cert is not None:
                emails = tuple(cert_emails(cert))
                cns = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
                signer = ", ".join(emails) or (str(cns[0].value) if cns else None)
                key_id = cert.fingerprint(hashes.SHA1()).hex().upper()
        if trusted.chain_trusted:
            status, detail = "verified", "certificate chain trusted"
        elif result.valid and cfg.no_verify:
            status, detail = "verified", "signer certificate not checked (SMIME::NoVerify)"
        elif result.valid:
            status = "signed_untrusted"
            detail = (
                _openssl_reason(trusted.detail) if not trusted.valid else ""
            ) or "certificate not trusted (no trust store)"
        else:
            reason = _openssl_reason(result.detail)
            status, detail = "verify_failed", reason or "bad signature"
        layer = self._check_sender(
            LayerResult("smime", "signature", status, signer, key_id, detail, emails)
        )
        return layer, result.content

    # -- the walk

    def walk(self, entity: bytes, depth: int = 0) -> bytes | None:
        """Return the replacement for *entity*, or ``None`` if unchanged."""
        if depth > _MAX_DEPTH:
            return None
        header_block, body = _split_entity(entity)
        head = _headers(header_block)
        ctype = head.get_content_type()
        pgp_on = self.config.pgp.enabled
        smime_on = self.config.smime.enabled

        if ctype == "multipart/encrypted" and "pgp-encrypted" in _param(head, "protocol"):
            if pgp_on:
                return self._pgp_mime_encrypted(head, body, depth)
            return None
        if ctype == "multipart/signed":
            protocol = _param(head, "protocol")
            if "pgp-signature" in protocol and pgp_on:
                return self._pgp_mime_signed(head, body, depth)
            if "pkcs7-signature" in protocol and smime_on:
                return self._smime_detached(entity, head, body, depth)
            return None
        if ctype in ("application/pkcs7-mime", "application/x-pkcs7-mime") and smime_on:
            smime_type = _param(head, "smime-type")
            if smime_type == "signed-data":
                return self._smime_opaque(entity, depth)
            if smime_type in ("enveloped-data", "authenveloped-data", ""):
                # No smime-type: Znuny treats non-"signed" pkcs7-mime as encrypted.
                return self._smime_enveloped(entity, depth)
            return None
        if ctype.startswith("multipart/"):
            boundary = _param(head, "boundary")
            split = _split_multipart(body, boundary) if boundary else None
            if split is None:
                return None
            new_parts: list[bytes] = []
            changed = False
            for part in split.parts:
                replaced = self.walk(part, depth + 1)
                changed = changed or replaced is not None
                new_parts.append(replaced if replaced is not None else part)
            if not changed:
                return None
            return header_block + b"\r\n" + split.join(new_parts)
        if pgp_on:
            return self._pgp_inline_leaf(entity)
        return None

    # PGP/MIME

    def _pgp_mime_encrypted(self, head: Message, body: bytes, depth: int) -> bytes | None:
        split = _split_multipart(body, _param(head, "boundary"))
        if split is None or len(split.parts) < 2:
            return None
        engine = self.pgp()
        if engine is None:
            self._unavailable("pgp", "encryption", self._pgp_error or "")
            return None
        _h, crypted = _split_entity(split.parts[1])
        crypted_msg = message_from_bytes(split.parts[1], policy=policy.compat32)
        decoded = crypted_msg.get_payload(decode=True)
        payload = decoded if isinstance(decoded, bytes) and decoded else crypted
        try:
            result = engine.decrypt(payload)
        except CryptoError as exc:
            self.layers.append(LayerResult("pgp", "encryption", "error", detail=str(exc)))
            return None
        if not result.ok:
            self.layers.append(
                LayerResult("pgp", "encryption", "decrypt_failed", detail=result.status)
            )
            return None
        self.layers.append(LayerResult("pgp", "encryption", "decrypted", detail="decryption ok"))
        if result.verify is not None:
            self.layers.append(self._pgp_signature_layer(result.verify))
        inner = _crlf(result.plaintext)
        self._remember_protected_subject(inner)
        nested = self.walk(inner, depth + 1)
        return nested if nested is not None else inner

    def _pgp_mime_signed(self, head: Message, body: bytes, depth: int) -> bytes | None:
        split = _split_multipart(body, _param(head, "boundary"))
        if split is None or len(split.parts) < 2:
            return None
        engine = self.pgp()
        signed_part = split.parts[0]
        if engine is None:
            self._unavailable("pgp", "signature", self._pgp_error or "")
        else:
            sig_msg = message_from_bytes(split.parts[1], policy=policy.compat32)
            sig = sig_msg.get_payload(decode=True)
            signature = sig if isinstance(sig, bytes) else b""
            try:
                verify = engine.verify_detached(signed_part, signature)
                self.layers.append(self._pgp_signature_layer(verify))
            except CryptoError as exc:
                self.layers.append(LayerResult("pgp", "signature", "error", detail=str(exc)))
        nested = self.walk(signed_part, depth + 1)
        return nested if nested is not None else signed_part

    def _pgp_inline_leaf(self, entity: bytes) -> bytes | None:
        msg = message_from_bytes(entity, policy=policy.compat32)
        ctype = msg.get_content_type()
        disposition = (msg.get_content_disposition() or "").lower()
        filename = msg.get_filename()
        if ctype == "text/plain" and disposition != "attachment" and not filename:
            text = _decode_text(msg)
            if "-----BEGIN PGP" not in text:
                return None
            return self._pgp_inline_text(text, msg.get_content_charset() or "utf-8")
        if filename:
            payload = msg.get_payload(decode=True)
            data = payload if isinstance(payload, bytes) else b""
            lower = filename.lower()
            is_armored = data.lstrip().startswith(_PGP_ARMOR_MESSAGE)
            if not (lower.endswith((".pgp", ".gpg")) or (lower.endswith(".asc") and is_armored)):
                return None
            engine = self.pgp()
            if engine is None:
                return None
            try:
                result = engine.decrypt(data)
            except CryptoError:
                return None
            if not result.ok:
                self.layers.append(
                    LayerResult("pgp", "encryption", "decrypt_failed", detail=result.status)
                )
                return None
            self.layers.append(
                LayerResult("pgp", "encryption", "decrypted", detail=f"attachment {filename}")
            )
            if result.verify is not None:
                self.layers.append(self._pgp_signature_layer(result.verify))
            m = _ENCRYPTED_EXT_RE.search(filename)
            new_name = (
                filename[: m.start()] + m.group(1)
                if m
                else re.sub(r"\.(?:pgp|gpg|asc)$", "", filename, flags=re.IGNORECASE)
            )
            return _attachment_entity(result.plaintext, new_name or "attachment")
        return None

    def _pgp_inline_text(self, text: str, charset: str) -> bytes | None:
        engine = self.pgp()
        if engine is None:
            self._unavailable("pgp", "encryption", self._pgp_error or "")
            return None
        changed = False
        encrypted = _PGP_MESSAGE_RE.search(text)
        if encrypted:
            try:
                result = engine.decrypt(encrypted.group(0).encode("utf-8"))
            except CryptoError as exc:
                self.layers.append(LayerResult("pgp", "encryption", "error", detail=str(exc)))
                return None
            if not result.ok:
                self.layers.append(
                    LayerResult("pgp", "encryption", "decrypt_failed", detail=result.status)
                )
                return None
            self.layers.append(
                LayerResult("pgp", "encryption", "decrypted", detail="decryption ok")
            )
            if result.verify is not None:
                self.layers.append(self._pgp_signature_layer(result.verify))
            plain = _decode_plain(result.plaintext, charset)
            text = text[: encrypted.start()] + plain + text[encrypted.end() :]
            changed = True
        signed = _PGP_SIGNED_RE.search(text)
        if signed:
            block = signed.group(0)
            try:
                verify = engine.verify(block.encode("utf-8"))
                self.layers.append(self._pgp_signature_layer(verify))
            except CryptoError as exc:
                self.layers.append(LayerResult("pgp", "signature", "error", detail=str(exc)))
                return _text_entity(text) if changed else None
            # Show the signed text without the armor (dash-escaping undone).
            content = re.sub(r"(?m)^- ", "", signed.group(2))
            text = text[: signed.start()] + content + text[signed.end() :]
            changed = True
        return _text_entity(text) if changed else None

    # S/MIME

    def _smime_detached(
        self, entity: bytes, head: Message, body: bytes, depth: int
    ) -> bytes | None:
        split = _split_multipart(body, _param(head, "boundary"))
        if split is None or len(split.parts) < 2:
            return None
        try:
            layer, _content = self._smime_verify(entity)
        except CryptoError as exc:
            self.layers.append(LayerResult("smime", "signature", "error", detail=str(exc)))
            return None
        self.layers.append(layer)
        signed_part = split.parts[0]
        nested = self.walk(signed_part, depth + 1)
        return nested if nested is not None else signed_part

    def _smime_opaque(self, entity: bytes, depth: int) -> bytes | None:
        try:
            layer, content = self._smime_verify(entity)
        except CryptoError as exc:
            self.layers.append(LayerResult("smime", "signature", "error", detail=str(exc)))
            return None
        self.layers.append(layer)
        if not content:
            return None
        inner = _crlf(content)
        nested = self.walk(inner, depth + 1)
        return nested if nested is not None else inner

    def _smime_enveloped(self, entity: bytes, depth: int) -> bytes | None:
        cfg = self.config.smime
        store = SmimeStore(cfg.cert_path, cfg.private_path, openssl_bin=cfg.openssl_bin)
        candidates: dict[str, Any] = {}
        try:
            for address in self.recipients:
                for entry in store.search(address, private=True):
                    candidates.setdefault(entry.filename, entry)
        except CryptoError as exc:
            self.layers.append(LayerResult("smime", "encryption", "error", detail=str(exc)))
            return None
        if not candidates:
            self.layers.append(
                LayerResult(
                    "smime",
                    "encryption",
                    "decrypt_failed",
                    detail="no private key found for the recipient addresses",
                )
            )
            return None
        engine = self.smime()
        last = ""
        for filename in sorted(candidates):
            paths = store.private_paths(filename)
            if paths is None:
                continue
            key_path, secret_path = paths
            secret = secret_path.read_text("utf-8") if secret_path.is_file() else None
            try:
                result = engine.decrypt(
                    entity, str(store.cert_dir / filename), str(key_path), secret=secret or None
                )
            except CryptoError as exc:
                last = str(exc)
                continue
            if result.ok:
                self.layers.append(
                    LayerResult(
                        "smime",
                        "encryption",
                        "decrypted",
                        key_id=filename,
                        detail=f"decrypted with {filename}",
                    )
                )
                inner = _crlf(result.plaintext)
                nested = self.walk(inner, depth + 1)
                return nested if nested is not None else inner
            last = _openssl_reason(result.detail)
        self.layers.append(
            LayerResult("smime", "encryption", "decrypt_failed", detail=last or "decryption failed")
        )
        return None

    def _remember_protected_subject(self, inner: bytes) -> None:
        header_block, _ = _split_entity(inner)
        head = _headers(header_block)
        if _param(head, "protected-headers") and head.get("Subject"):
            from email.header import decode_header, make_header

            try:
                self.protected_subject = str(make_header(decode_header(str(head["Subject"]))))
            except (UnicodeDecodeError, LookupError, ValueError):
                self.protected_subject = str(head["Subject"])


def _openssl_reason(stderr: str) -> str:
    """The first meaningful line of openssl's error output (≤ 200 chars)."""
    for line in (stderr or "").splitlines():
        line = line.strip()
        if not line or line.lower().startswith("verification successful"):
            continue
        # "40E7…:error:10800075:PKCS7 routines:…:certificate verify error:…:
        #  Verify error:unable to get local issuer certificate"
        if ":error:" in line:
            tail = line.split(":")
            reason = [t for t in tail if t and not t.startswith(("../", "crypto/"))]
            if "Verify error" in line:
                return "certificate not trusted: " + line.rsplit("Verify error:", 1)[-1][:160]
            return (reason[-1] if reason else line)[:200]
        return line[:200]
    return ""


def is_crypto_candidate(raw: bytes) -> bool:
    """Cheap pre-check: does the mail contain anything the walk would touch?"""
    lowered = raw.lower()
    return (
        b"multipart/encrypted" in lowered
        or b"multipart/signed" in lowered
        or b"pkcs7-mime" in lowered
        or b"-----begin pgp" in lowered
        or b".pgp" in lowered
        or b".gpg" in lowered
    )


def walk_message(raw: bytes, config: CryptoConfig) -> WalkOutcome:
    """Decrypt/verify every crypto layer of *raw* (a complete RFC 822 mail)."""
    if not (config.pgp.enabled or config.smime.enabled) or not is_crypto_candidate(raw):
        return WalkOutcome(security=None)
    entity = _crlf(raw)
    header_block, _ = _split_entity(entity)
    walker = _Walker(config, _headers(header_block))
    try:
        content = walker.walk(entity)
    except CryptoUnavailableError as exc:
        walker.layers.append(
            LayerResult(_guess_method(raw), "signature", "unavailable", detail=str(exc))
        )
        content = None
    return WalkOutcome(
        security=combine_layers(walker.layers),
        content=content,
        layers=walker.layers,
        protected_subject=walker.protected_subject,
    )


def _guess_method(raw: bytes) -> str:
    return "smime" if b"pkcs7" in raw.lower() else "pgp"


__all__ = [
    "LayerResult",
    "SECURITY_STATUSES",
    "SecurityResult",
    "WalkOutcome",
    "combine_layers",
    "is_crypto_candidate",
    "walk_message",
]
