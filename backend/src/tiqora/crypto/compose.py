"""Compose-side email security: key choice, validation and compose options.

Port of Znuny's ``Kernel::Output::HTML::ArticleCompose::{Security,Sign,
Crypt}``:

* **sign keys** are the secret keys / private certificates for the sender
  (``From`` = the queue's system address); the queue's ``default_sign_key``
  (``PGP::Detached::<id>``, ``PGP::Inline::<id>``, ``SMIME::Detached::<file>``)
  is preselected;
* **encrypt keys** default to the first usable (not expired/revoked) key of
  every ``To``/``Cc``/``Bcc`` recipient; an explicit selection must cover
  every recipient (Znuny ``_CheckRecipient``), and expired/revoked keys are
  refused;
* requesting encryption with a recipient left uncovered is an error — never
  a silent unencrypted send.

:func:`resolve_plan` turns an API :class:`EmailSecurityIn` into the
:class:`~tiqora.crypto.mime_build.SecurityPlan` the builder consumes;
:func:`crypto_options` feeds the compose UI
(``GET /tickets/{id}/crypto-options``).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import getaddresses
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.crypto import CryptoError
from tiqora.crypto.config import CryptoConfig
from tiqora.crypto.keystore import SIGN_KEY_RE
from tiqora.crypto.mime_build import SecurityPlan
from tiqora.crypto.pgp import PgpEngine, PgpKeyInfo
from tiqora.crypto.smime_store import FILENAME_RE, SmimeEntry, SmimeStore

Backend = Literal["pgp", "smime"]
Method = Literal["detached", "inline"]


class EmailSecurityIn(BaseModel):
    """``email_security`` of an outgoing email (reply, forward, new email ticket).

    Signing happens when ``sign_key`` is set (PGP key id/fingerprint or
    S/MIME ``<hash>.<n>``; the Znuny form ``PGP::Detached::<id>`` is
    accepted too). ``encrypt`` encrypts for ``encrypt_keys`` or — when
    omitted — for the first usable key of every recipient.
    """

    backend: Backend
    method: Method = "detached"
    sign_key: str | None = None
    encrypt: bool = False
    encrypt_keys: list[str] | None = Field(default=None, max_length=100)

    @property
    def active(self) -> bool:
        return bool(self.sign_key) or self.encrypt


@dataclass(frozen=True)
class KeyProblem:
    subject: str  # recipient address or key reference
    reason: str  # missing | expired | revoked | invalid | no_selected_key | unknown_key | ...

    def text(self) -> str:
        return f"{self.subject} ({self.reason})"


class EmailSecurityError(CryptoError):
    """The requested signing/encryption cannot be done — the send must not happen."""

    def __init__(self, message: str, problems: list[KeyProblem] | None = None) -> None:
        self.problems = problems or []
        if self.problems:
            message = f"{message}: " + ", ".join(p.text() for p in self.problems)
        super().__init__(message)


# ------------------------------------------------------------------ helpers


def split_addresses(*fields: str | None) -> list[str]:
    out: list[str] = []
    for _name, addr in getaddresses([f for f in fields if f]):
        low = addr.strip().lower()
        if low and "@" in low and low not in out:
            out.append(low)
    return out


def _strip_znuny_prefix(ref: str) -> tuple[str | None, str]:
    """``PGP::Detached::ABCD`` → (``detached``, ``ABCD``); plain refs unchanged."""
    m = SIGN_KEY_RE.match(ref.strip())
    if m is None:
        return None, ref.strip()
    if m.group("pgp_key"):
        return m.group("pgp_method").lower(), m.group("pgp_key")
    return "detached", m.group("smime_file")


def _smime_status(entry: SmimeEntry, now: datetime) -> str:
    if entry.info is None:
        return "invalid"
    return "expired" if entry.info.is_expired(now) else "valid"


def _pgp_usable(key: PgpKeyInfo) -> bool:
    return key.status == "good"


def _pgp_label(key: PgpKeyInfo) -> str:
    uid = key.uids[0] if key.uids else ""
    return f"{key.znuny_key_id} {uid}".strip()


def _smime_label(entry: SmimeEntry) -> str:
    info = entry.info
    if info is None:
        return entry.filename
    return f"{entry.filename} {info.email_joined} [{info.short_end_date.isoformat()}]"


# ------------------------------------------------------------ plan resolution


def _resolve_pgp(
    config: CryptoConfig, sec: EmailSecurityIn, recipients: list[str], check_recipients: bool
) -> SecurityPlan:
    if not config.pgp.enabled:
        raise EmailSecurityError("PGP is not enabled")
    keys = PgpEngine.from_config(config.pgp).list_keys()
    problems: list[KeyProblem] = []
    sign_fp: str | None = None
    signer: str | None = None
    method = sec.method
    if sec.sign_key:
        prefix_method, ref = _strip_znuny_prefix(sec.sign_key)
        method = prefix_method or method  # type: ignore[assignment]
        key = next((k for k in keys if k.matches(ref)), None)
        if key is None or not key.has_secret:
            raise EmailSecurityError(f"no PGP secret key {ref!r} for signing")
        if not _pgp_usable(key):
            raise EmailSecurityError(f"cannot sign with {key.status} PGP key {ref!r}")
        sign_fp, signer = key.fingerprint, (key.uids[0] if key.uids else key.fingerprint)

    encrypt_fps: list[str] = []
    if sec.encrypt:
        if not recipients and not (sec.encrypt_keys and not check_recipients):
            raise EmailSecurityError("encryption requested but the mail has no recipients")
        if sec.encrypt_keys:
            selected: list[PgpKeyInfo] = []
            for ref in sec.encrypt_keys:
                key = next((k for k in keys if k.matches(ref)), None)
                if key is None:
                    problems.append(KeyProblem(ref, "unknown_key"))
                elif not _pgp_usable(key):
                    problems.append(KeyProblem(", ".join(key.emails) or ref, key.status))
                else:
                    selected.append(key)
            for addr in recipients if check_recipients else []:
                if any(addr in k.emails for k in selected):
                    continue
                if any(p.subject == addr for p in problems) or any(
                    addr in p.subject.split(", ") for p in problems
                ):
                    continue
                has_any = any(addr in k.emails for k in keys)
                problems.append(KeyProblem(addr, "no_selected_key" if has_any else "missing"))
            encrypt_fps = [k.fingerprint for k in selected]
        else:
            for addr in recipients:
                candidates = [k for k in keys if addr in k.emails]
                usable = [k for k in candidates if _pgp_usable(k)]
                if usable:
                    encrypt_fps.append(usable[0].fingerprint)
                elif candidates:
                    problems.append(KeyProblem(addr, candidates[0].status))
                else:
                    problems.append(KeyProblem(addr, "missing"))
    if problems:
        raise EmailSecurityError("no usable PGP encryption key", problems)
    return SecurityPlan(
        backend="pgp",
        method=method,
        sign_key=sign_fp,
        encrypt_keys=tuple(dict.fromkeys(encrypt_fps)),
        signer_label=signer,
    )


def _resolve_smime(
    config: CryptoConfig, sec: EmailSecurityIn, recipients: list[str], check_recipients: bool
) -> SecurityPlan:
    if not config.smime.enabled:
        raise EmailSecurityError("S/MIME is not enabled")
    store = SmimeStore.from_config(config.smime)
    entries = [e for e in store.list_entries() if e.info is not None]
    now = datetime.now(UTC)
    problems: list[KeyProblem] = []
    sign_file: str | None = None
    signer: str | None = None
    if sec.sign_key:
        _m, ref = _strip_znuny_prefix(sec.sign_key)
        if FILENAME_RE.match(ref):
            entry = next((e for e in entries if e.filename == ref), None)
        else:  # an email address, as GenericInterface EmailSecurity allows
            entry = next(
                (
                    e
                    for e in entries
                    if e.has_private
                    and e.info
                    and ref.lower() in e.info.emails
                    and not e.info.is_expired(now)
                ),
                None,
            )
        if entry is None or not entry.has_private:
            raise EmailSecurityError(f"no S/MIME private key {ref!r} for signing")
        if _smime_status(entry, now) != "valid":
            raise EmailSecurityError(f"cannot sign with expired S/MIME certificate {ref!r}")
        sign_file = entry.filename
        signer = entry.info.email_joined if entry.info else entry.filename

    encrypt_files: list[str] = []
    if sec.encrypt:
        if not recipients and not (sec.encrypt_keys and not check_recipients):
            raise EmailSecurityError("encryption requested but the mail has no recipients")
        certs = [e for e in entries if e.info is not None and not e.info.is_ca]
        if sec.encrypt_keys:
            selected: list[SmimeEntry] = []
            for ref in sec.encrypt_keys:
                entry = next(
                    (
                        e
                        for e in certs
                        if e.filename == ref or (e.info and ref.lower() in e.info.emails)
                    ),
                    None,
                )
                if entry is None:
                    problems.append(KeyProblem(ref, "unknown_key"))
                elif _smime_status(entry, now) != "valid":
                    problems.append(KeyProblem(ref, "expired"))
                else:
                    selected.append(entry)
            for addr in recipients if check_recipients else []:
                if any(e.info and addr in e.info.emails for e in selected):
                    continue
                has_any = any(e.info and addr in e.info.emails for e in certs)
                problems.append(KeyProblem(addr, "no_selected_key" if has_any else "missing"))
            encrypt_files = [e.filename for e in selected]
        else:
            for addr in recipients:
                candidates = [e for e in certs if e.info and addr in e.info.emails]
                usable = [e for e in candidates if _smime_status(e, now) == "valid"]
                if usable:
                    encrypt_files.append(usable[0].filename)
                else:
                    problems.append(KeyProblem(addr, "expired" if candidates else "missing"))
    if problems:
        raise EmailSecurityError("no usable S/MIME certificate", problems)
    return SecurityPlan(
        backend="smime",
        method="detached",
        sign_key=sign_file,
        encrypt_keys=tuple(dict.fromkeys(encrypt_files)),
        signer_label=signer,
    )


def resolve_plan_sync(
    config: CryptoConfig,
    sec: EmailSecurityIn,
    *,
    recipients: list[str],
    check_recipients: bool = True,
) -> SecurityPlan | None:
    """Validate *sec* against the key stores; ``None`` when it asks for nothing.

    ``check_recipients=False`` accepts explicit ``encrypt_keys`` without
    checking that they cover the recipients (GenericInterface articles that
    are only stored, never sent).
    """
    if not sec.active:
        return None
    try:
        if sec.backend == "pgp":
            return _resolve_pgp(config, sec, recipients, check_recipients)
        return _resolve_smime(config, sec, recipients, check_recipients)
    except EmailSecurityError:
        raise
    except CryptoError as exc:
        raise EmailSecurityError(f"{sec.backend} unavailable: {exc}") from exc


async def smime_chain_paths(
    session: AsyncSession | None, config: CryptoConfig, filename: str
) -> tuple[str, ...]:
    """CA certificates linked to *filename* by signer relations (Znuny ``-certfile``)."""
    if session is None:
        return ()
    from tiqora.crypto.keystore import list_smime_relations

    store = SmimeStore.from_config(config.smime)
    try:
        relations = await list_smime_relations(session, store, filename)
    except Exception:  # noqa: BLE001 — relation table absent: no chain, still signs
        return ()
    return tuple(
        str(Path(store.cert_dir) / r.ca_filename) for r in relations if r.ca_filename is not None
    )


async def resolve_plan(
    session: AsyncSession | None,
    config: CryptoConfig,
    sec: EmailSecurityIn,
    *,
    recipients: list[str],
) -> SecurityPlan | None:
    plan = await asyncio.to_thread(resolve_plan_sync, config, sec, recipients=recipients)
    if plan is not None and plan.backend == "smime" and plan.sign_key:
        chain = await smime_chain_paths(session, config, plan.sign_key)
        if chain:
            from dataclasses import replace

            plan = replace(plan, smime_chain=chain)
    return plan


# -------------------------------------------------------------- compose options


class CryptoComposeKeyOut(BaseModel):
    key: str  # value for sign_key / encrypt_keys
    label: str
    status: str  # PGP: good|expired|revoked; S/MIME: valid|expired
    usable: bool
    expires: datetime | None = None
    emails: list[str] = Field(default_factory=list)


class CryptoComposeRecipientOut(BaseModel):
    address: str
    #: ok | missing | expired | revoked (no usable key)
    status: str
    keys: list[CryptoComposeKeyOut] = Field(default_factory=list)
    #: preselected encrypt key(s): the first usable key
    selected: list[str] = Field(default_factory=list)


class CryptoComposeBackendOut(BaseModel):
    backend: Backend
    available: bool
    problem: str | None = None
    methods: list[Method]
    sign_keys: list[CryptoComposeKeyOut] = Field(default_factory=list)
    recipients: list[CryptoComposeRecipientOut] = Field(default_factory=list)
    #: every recipient has a usable key
    can_encrypt: bool = False


class CryptoOptionsOut(BaseModel):
    enabled: bool
    from_address: str | None = None
    backends: list[CryptoComposeBackendOut] = Field(default_factory=list)
    #: preselection: the queue's default sign key, else ``None`` (no security);
    #: with the queue policy applied (tiqora.crypto.queue_security) it also
    #: carries ``encrypt`` when the queue encrypts this mail
    default: EmailSecurityIn | None = None
    warnings: list[str] = Field(default_factory=list)
    #: the queue's usable sign key as such (independent of the sign default)
    queue_sign: EmailSecurityIn | None = None
    #: modes the composer offers: none | sign | encrypt | sign_encrypt
    #: (empty = no security control at all)
    modes: list[str] = Field(default_factory=list)
    #: queue policy: sign by default, encryption off | auto | required
    sign_default: bool = True
    encrypt_policy: str = "off"
    #: set when the queue requires encryption and this mail cannot be encrypted
    blocked: str | None = None


def _pgp_options(
    config: CryptoConfig, sender: str | None, recipients: list[str], extra_sign: str | None
) -> CryptoComposeBackendOut:
    keys = PgpEngine.from_config(config.pgp).list_keys()
    sign: list[CryptoComposeKeyOut] = []
    for k in keys:
        if not k.has_secret:
            continue
        if sender and sender not in k.emails and not (extra_sign and k.matches(extra_sign)):
            continue
        sign.append(
            CryptoComposeKeyOut(
                key=k.znuny_key_id,
                label=_pgp_label(k),
                status=k.status,
                usable=_pgp_usable(k),
                expires=k.expires,
                emails=k.emails,
            )
        )
    rcpts: list[CryptoComposeRecipientOut] = []
    for addr in recipients:
        cands = [k for k in keys if addr in k.emails]
        outs = [
            CryptoComposeKeyOut(
                key=k.fingerprint,
                label=_pgp_label(k),
                status=k.status,
                usable=_pgp_usable(k),
                expires=k.expires,
                emails=k.emails,
            )
            for k in cands
        ]
        usable = [o for o in outs if o.usable]
        status = "ok" if usable else (outs[0].status if outs else "missing")
        rcpts.append(
            CryptoComposeRecipientOut(
                address=addr,
                status=status,
                keys=outs,
                selected=[usable[0].key] if usable else [],
            )
        )
    return CryptoComposeBackendOut(
        backend="pgp",
        available=True,
        methods=["detached", "inline"],
        sign_keys=sign,
        recipients=rcpts,
        can_encrypt=bool(rcpts) and all(r.status == "ok" for r in rcpts),
    )


def _smime_options(
    config: CryptoConfig, sender: str | None, recipients: list[str], extra_sign: str | None
) -> CryptoComposeBackendOut:
    store = SmimeStore.from_config(config.smime)
    entries = [e for e in store.list_entries() if e.info is not None]
    now = datetime.now(UTC)

    def out(e: SmimeEntry) -> CryptoComposeKeyOut:
        assert e.info is not None
        status = _smime_status(e, now)
        return CryptoComposeKeyOut(
            key=e.filename,
            label=_smime_label(e),
            status=status,
            usable=status == "valid",
            expires=e.info.not_after,
            emails=e.info.emails,
        )

    sign = [
        out(e)
        for e in entries
        if e.has_private
        and e.info is not None
        and (not sender or sender in e.info.emails or e.filename == extra_sign)
    ]
    rcpts: list[CryptoComposeRecipientOut] = []
    for addr in recipients:
        outs = [out(e) for e in entries if e.info and not e.info.is_ca and addr in e.info.emails]
        usable = [o for o in outs if o.usable]
        rcpts.append(
            CryptoComposeRecipientOut(
                address=addr,
                status="ok" if usable else ("expired" if outs else "missing"),
                keys=outs,
                selected=[usable[0].key] if usable else [],
            )
        )
    return CryptoComposeBackendOut(
        backend="smime",
        available=True,
        methods=["detached"],
        sign_keys=sign,
        recipients=rcpts,
        can_encrypt=bool(rcpts) and all(r.status == "ok" for r in rcpts),
    )


def crypto_options_sync(
    config: CryptoConfig,
    *,
    from_address: str | None,
    recipients: list[str],
    default_sign_key: str | None,
) -> CryptoOptionsOut:
    sender = next(iter(split_addresses(from_address)), None)
    result = CryptoOptionsOut(
        enabled=config.pgp.enabled or config.smime.enabled, from_address=sender
    )
    default_method: str | None = None
    default_ref: str | None = None
    default_backend: str | None = None
    if default_sign_key:
        m = SIGN_KEY_RE.match(default_sign_key)
        if m is None:
            result.warnings.append(f"queue default sign key {default_sign_key!r} is malformed")
        elif m.group("pgp_key"):
            default_backend, default_method = "pgp", m.group("pgp_method").lower()
            default_ref = m.group("pgp_key")
        else:
            default_backend, default_method = "smime", "detached"
            default_ref = m.group("smime_file")

    for backend, enabled, build in (
        ("pgp", config.pgp.enabled, _pgp_options),
        ("smime", config.smime.enabled, _smime_options),
    ):
        if not enabled:
            continue
        extra = default_ref if default_backend == backend else None
        try:
            opts = build(config, sender, recipients, extra)
        except CryptoError as exc:
            opts = CryptoComposeBackendOut(
                backend=backend,  # type: ignore[arg-type]
                available=False,
                problem=str(exc),
                methods=["detached"],
            )
        result.backends.append(opts)
        if extra and opts.available:
            want = extra
            if backend == "pgp":
                # The stored id may be any id of the key (Znuny shows the
                # last subkey's short id) — map it to the listed id.
                try:
                    found = PgpEngine.from_config(config.pgp).find_key(extra)
                except CryptoError:
                    found = None
                want = found.znuny_key_id if found is not None else extra.upper()
            match = next((k for k in opts.sign_keys if k.key == want), None)
            if match is None:
                result.warnings.append(f"queue default sign key {default_sign_key} not found")
            elif not match.usable:
                result.warnings.append(
                    f"queue default sign key {default_sign_key} is {match.status}"
                )
            else:
                result.default = EmailSecurityIn(
                    backend=backend,  # type: ignore[arg-type]
                    method=default_method,  # type: ignore[arg-type]
                    sign_key=match.key,
                )
    if default_backend and not any(b.backend == default_backend for b in result.backends):
        result.warnings.append(
            f"queue default sign key {default_sign_key} needs {default_backend}, which is disabled"
        )
    return result


async def crypto_options(
    config: CryptoConfig,
    *,
    from_address: str | None,
    recipients: list[str],
    default_sign_key: str | None,
) -> CryptoOptionsOut:
    return await asyncio.to_thread(
        crypto_options_sync,
        config,
        from_address=from_address,
        recipients=recipients,
        default_sign_key=default_sign_key,
    )


__all__ = [
    "CryptoComposeBackendOut",
    "CryptoComposeKeyOut",
    "CryptoOptionsOut",
    "CryptoComposeRecipientOut",
    "EmailSecurityError",
    "EmailSecurityIn",
    "KeyProblem",
    "crypto_options",
    "crypto_options_sync",
    "resolve_plan",
    "resolve_plan_sync",
    "split_addresses",
]
