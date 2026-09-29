"""Notification email security (Znuny ``Transport::Email::SecurityOptionsGet``).

Real gpg/openssl keyrings built at runtime (``CryptoWorld``): the support
mailbox has a PGP secret key + S/MIME private certificate, the customer's
public key/certificate is known. Covers the item parsing, key choice (queue
default sign key vs first sender key), the missing-key policies and the
backend-unavailable skip — each exactly as Znuny decides.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from typing import Any

import pytest

from tests._crypto_mail_fixtures import CUSTOMER, SUPPORT, CryptoWorld, gen_pgp_key
from tiqora.crypto.mime_build import SecurityPlan
from tiqora.crypto.notification import (
    NotificationSecurityConfig,
    default_sign_key_ref,
    notification_security_sync,
    validate_security_items,
)
from tiqora.crypto.pgp import PgpEngine
from tiqora.crypto.smime_store import SmimeStore

pytestmark = [
    pytest.mark.skipif(shutil.which("gpg") is None, reason="gpg binary not on PATH"),
    pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl binary not on PATH"),
]

NOKEY = "nokey@example.net"


@pytest.fixture(scope="module")
def world() -> Iterator[CryptoWorld]:
    pytest.importorskip("gnupg")
    w = CryptoWorld.create()
    SmimeStore(w.cert_dir, w.private_dir).add_certificate(w.customer_cert.cert_pem)
    # a second secret key for the support address: the queue default must win
    w.files["second_fp"] = gen_pgp_key(w.support_home, "Tiqora Support Two", SUPPORT)
    yield w
    w.cleanup()


def _sec(level: str, *, sign: str = "", crypt: str = "", enabled: str = "1") -> Any:
    items: dict[str, list[str]] = {"EmailSecuritySettings": [enabled] if enabled else []}
    items["EmailSigningCrypting"] = [level]
    if sign:
        items["EmailMissingSigningKeys"] = [sign]
    if crypt:
        items["EmailMissingCryptingKeys"] = [crypt]
    return NotificationSecurityConfig.from_items(items)


def _decide(world: CryptoWorld, sec: Any, **kw: Any) -> Any:
    params: dict[str, Any] = {
        "notification_name": "Ticket create notification",
        "sender_email": SUPPORT,
        "recipient_email": CUSTOMER,
    }
    params.update(kw)
    config = params.pop("config", None) or world.config()
    return notification_security_sync(config, sec, **params)


def _first_support_key(world: CryptoWorld) -> str:
    keys = [
        k
        for k in PgpEngine.from_config(world.config().pgp).list_keys()
        if k.has_secret and SUPPORT in k.emails
    ]
    return keys[0].fingerprint


def test_items_parse_like_znuny() -> None:
    assert not _sec("PGPSign", enabled="").active  # checkbox off
    assert not _sec("").active  # no level chosen
    sec = _sec("SMIMESignCrypt", sign="Skip", crypt="Send")
    assert sec.active and sec.backend == "smime" and sec.signs and sec.encrypts
    assert sec.on_missing_sign == "Skip" and sec.on_missing_crypt == "Send"
    pgp_crypt = _sec("PGPCrypt")
    assert pgp_crypt.backend == "pgp" and pgp_crypt.encrypts and not pgp_crypt.signs


def test_validate_security_items() -> None:
    assert validate_security_items({}) == []
    assert (
        validate_security_items(
            {
                "EmailSecuritySettings": ["1"],
                "EmailSigningCrypting": ["PGPSignCrypt"],
                "EmailMissingSigningKeys": ["Skip"],
                "EmailMissingCryptingKeys": ["Send"],
            }
        )
        == []
    )
    problems = validate_security_items(
        {"EmailSigningCrypting": ["GPGSign"], "EmailMissingCryptingKeys": ["Drop"]}
    )
    assert len(problems) == 2


def test_default_sign_key_ref_znuny_forms() -> None:
    assert default_sign_key_ref("PGP::Detached::81877F5E") == "81877F5E"
    assert default_sign_key_ref("PGP::Inline::81877F5E") == "81877F5E"
    assert default_sign_key_ref("SMIME::Detached::abcd1234.0") == "abcd1234.0"
    assert default_sign_key_ref("SMIME::abcd1234.0") == "abcd1234.0"  # legacy
    assert default_sign_key_ref(None) is None


def test_inactive_is_plain(world: CryptoWorld) -> None:
    decision = _decide(world, _sec("PGPSignCrypt", enabled=""))
    assert not decision.skip and decision.plan is None and decision.log == []


def test_pgp_sign_and_encrypt_picks_first_sender_key_and_recipient_key(
    world: CryptoWorld,
) -> None:
    decision = _decide(world, _sec("PGPSignCrypt"))
    assert not decision.skip
    assert decision.plan == SecurityPlan(
        backend="pgp",
        method="detached",
        sign_key=_first_support_key(world),
        encrypt_keys=(world.customer_fp,),
        signer_label=decision.plan.signer_label,
    )


def test_queue_default_sign_key_wins_when_it_is_a_sender_key(world: CryptoWorld) -> None:
    second = world.files["second_fp"]
    assert _first_support_key(world) != second
    decision = _decide(
        world, _sec("PGPSign"), queue_default_sign_key=f"PGP::Detached::{second[-8:]}"
    )
    assert decision.plan is not None and decision.plan.sign_key == second
    assert decision.plan.encrypt_keys == ()
    # a default of the other backend does not match: first sender key
    other = _decide(world, _sec("PGPSign"), queue_default_sign_key="SMIME::Detached::abcd1234.0")
    assert other.plan is not None and other.plan.sign_key == _first_support_key(world)


def test_missing_sign_key_skip_or_send_unsigned(world: CryptoWorld) -> None:
    skipped = _decide(world, _sec("PGPSignCrypt", sign="Skip"), sender_email=NOKEY)
    assert skipped.skip and skipped.plan is None
    assert skipped.log == [
        (
            "notice",
            "Could not sign notification 'Ticket create notification' due to missing PGP"
            f" sign key for '{NOKEY}', skipping notification distribution!",
        )
    ]
    unsigned = _decide(world, _sec("PGPSignCrypt", sign="Send"), sender_email=NOKEY)
    assert not unsigned.skip and unsigned.plan is not None
    assert unsigned.plan.sign_key is None and unsigned.plan.encrypt_keys == (world.customer_fp,)
    assert unsigned.log[0][1].endswith(", sending unsigned!")
    # no policy stored = Znuny's default branch: send unsigned
    default = _decide(world, _sec("PGPSign"), sender_email=NOKEY)
    assert not default.skip and default.plan is None


def test_missing_encryption_key_skip_or_send_unencrypted(world: CryptoWorld) -> None:
    skipped = _decide(world, _sec("SMIMESignCrypt", crypt="Skip"), recipient_email=NOKEY)
    assert skipped.skip
    assert skipped.log[0][1].startswith(
        "Could not encrypt notification 'Ticket create notification' due to missing SMIME"
        f" encryption key for '{NOKEY}'"
    )
    sent = _decide(world, _sec("SMIMESignCrypt", crypt="Send"), recipient_email=NOKEY)
    assert not sent.skip and sent.plan is not None
    assert sent.plan.backend == "smime" and sent.plan.sign_key is not None
    assert sent.plan.encrypt_keys == ()
    assert sent.log[0][1].endswith(", sending unencrypted!")
    # encrypt-only with nothing to encrypt for and "Send": plain mail
    plain = _decide(world, _sec("PGPCrypt", crypt="Send"), recipient_email=NOKEY)
    assert not plain.skip and plain.plan is None


def test_smime_sign_and_encrypt_uses_store_files(world: CryptoWorld) -> None:
    store = SmimeStore(world.cert_dir, world.private_dir)
    entries = {e.info.emails[0]: e for e in store.list_entries() if e.info and not e.info.is_ca}
    decision = _decide(world, _sec("SMIMESignCrypt"))
    assert decision.plan is not None
    assert decision.plan.sign_key == entries[SUPPORT].filename
    assert decision.plan.encrypt_keys == (entries[CUSTOMER].filename,)


def test_disabled_backend_skips_the_notification(world: CryptoWorld) -> None:
    decision = _decide(world, _sec("PGPSign"), config=world.config(pgp=False))
    assert decision.skip and decision.log == [("error", "No PGP support!")]


def test_pgp_method_inline_only_without_rich_text(world: CryptoWorld) -> None:
    rich = _decide(world, _sec("PGPSign"), pgp_method="Inline")
    assert rich.plan is not None and rich.plan.method == "detached"
    plain = _decide(world, _sec("PGPSign"), pgp_method="Inline", rich_text=False)
    assert plain.plan is not None and plain.plan.method == "inline"
