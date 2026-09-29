"""Compose-side key choice (tiqora.crypto.compose) against real keyrings/stores.

Znuny ``ArticleCompose::{Sign,Crypt}`` semantics: encrypt keys default to the
first usable key per recipient, an explicit selection must cover every
recipient, expired/revoked keys are refused, and the queue default sign key
is preselected in the compose options.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from tests._crypto_mail_fixtures import CUSTOMER, SUPPORT, CryptoWorld
from tests._smime_fixtures import make_leaf
from tiqora.crypto.compose import (
    EmailSecurityError,
    EmailSecurityIn,
    crypto_options_sync,
    resolve_plan_sync,
    split_addresses,
)
from tiqora.crypto.pgp import PgpEngine
from tiqora.crypto.smime_store import SmimeStore

pytestmark = [
    pytest.mark.skipif(shutil.which("gpg") is None, reason="gpg binary not on PATH"),
    pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl binary not on PATH"),
]

OLD = "old@example.com"


@pytest.fixture(scope="module")
def world() -> Iterator[CryptoWorld]:
    pytest.importorskip("gnupg")
    w = CryptoWorld.create()
    store = SmimeStore(w.cert_dir, w.private_dir)
    store.add_certificate(w.customer_cert.cert_pem)
    now = datetime.now(UTC)
    store.add_certificate(
        make_leaf(
            OLD, ca=w.ca, not_before=now - timedelta(days=60), not_after=now - timedelta(days=1)
        ).cert_pem
    )
    yield w
    w.cleanup()


def _support_znuny_id(world: CryptoWorld) -> str:
    key = PgpEngine.from_config(world.config().pgp).find_key(world.support_fp)
    assert key is not None
    return key.znuny_key_id


def test_split_addresses_dedupes_and_lowercases() -> None:
    assert split_addresses("A <A@x.org>, b@y.org", None, "a@x.org") == ["a@x.org", "b@y.org"]


def test_pgp_default_keys_per_recipient_and_sign_key(world: CryptoWorld) -> None:
    plan = resolve_plan_sync(
        world.config(),
        EmailSecurityIn(
            backend="pgp", sign_key=f"PGP::Inline::{_support_znuny_id(world)}", encrypt=True
        ),
        recipients=[CUSTOMER],
    )
    assert plan is not None
    assert plan.method == "inline"  # taken from the Znuny-form sign key
    assert plan.sign_key == world.support_fp
    assert plan.encrypt_keys == (world.customer_fp,)


def test_pgp_missing_recipient_key_lists_address(world: CryptoWorld) -> None:
    with pytest.raises(EmailSecurityError) as exc:
        resolve_plan_sync(
            world.config(),
            EmailSecurityIn(backend="pgp", encrypt=True),
            recipients=[CUSTOMER, "nokey@example.net"],
        )
    assert [(p.subject, p.reason) for p in exc.value.problems] == [("nokey@example.net", "missing")]
    assert "nokey@example.net (missing)" in str(exc.value)


def test_pgp_explicit_selection_must_cover_every_recipient(world: CryptoWorld) -> None:
    with pytest.raises(EmailSecurityError) as exc:
        resolve_plan_sync(
            world.config(),
            EmailSecurityIn(backend="pgp", encrypt=True, encrypt_keys=[world.support_fp]),
            recipients=[CUSTOMER],
        )
    assert [(p.subject, p.reason) for p in exc.value.problems] == [(CUSTOMER, "no_selected_key")]
    with pytest.raises(EmailSecurityError) as exc2:
        resolve_plan_sync(
            world.config(),
            EmailSecurityIn(backend="pgp", encrypt=True, encrypt_keys=["DEADBEEFDEADBEEF"]),
            recipients=[CUSTOMER],
        )
    assert ("DEADBEEFDEADBEEF", "unknown_key") in [
        (p.subject, p.reason) for p in exc2.value.problems
    ]


def test_pgp_sign_key_without_secret_is_refused(world: CryptoWorld) -> None:
    with pytest.raises(EmailSecurityError, match="no PGP secret key"):
        resolve_plan_sync(
            world.config(),
            EmailSecurityIn(backend="pgp", sign_key=world.customer_fp),
            recipients=[CUSTOMER],
        )


def test_nothing_requested_is_no_plan(world: CryptoWorld) -> None:
    assert (
        resolve_plan_sync(world.config(), EmailSecurityIn(backend="pgp"), recipients=[CUSTOMER])
        is None
    )


def test_smime_expired_recipient_certificate_is_refused(world: CryptoWorld) -> None:
    plan = resolve_plan_sync(
        world.config(),
        EmailSecurityIn(backend="smime", sign_key=SUPPORT, encrypt=True),
        recipients=[CUSTOMER],
    )
    assert plan is not None and plan.sign_key and len(plan.encrypt_keys) == 1
    with pytest.raises(EmailSecurityError) as exc:
        resolve_plan_sync(
            world.config(),
            EmailSecurityIn(backend="smime", encrypt=True),
            recipients=[CUSTOMER, OLD],
        )
    assert [(p.subject, p.reason) for p in exc.value.problems] == [(OLD, "expired")]


def test_disabled_backend_is_an_error(world: CryptoWorld) -> None:
    with pytest.raises(EmailSecurityError, match="not enabled"):
        resolve_plan_sync(
            world.config(pgp=False),
            EmailSecurityIn(backend="pgp", encrypt=True),
            recipients=[CUSTOMER],
        )


def test_compose_options_preselect_queue_default_and_mark_recipients(world: CryptoWorld) -> None:
    znuny_id = _support_znuny_id(world)
    opts = crypto_options_sync(
        world.config(),
        from_address=f"Support <{SUPPORT}>",
        recipients=[CUSTOMER, "nokey@example.net", OLD],
        default_sign_key=f"PGP::Detached::{znuny_id}",
    )
    assert opts.enabled and opts.from_address == SUPPORT
    assert opts.default is not None
    assert (opts.default.backend, opts.default.method, opts.default.sign_key) == (
        "pgp",
        "detached",
        znuny_id,
    )
    pgp = next(b for b in opts.backends if b.backend == "pgp")
    assert [k.key for k in pgp.sign_keys] == [znuny_id]
    status = {r.address: r.status for r in pgp.recipients}
    assert status == {CUSTOMER: "ok", "nokey@example.net": "missing", OLD: "missing"}
    assert pgp.recipients[0].selected == [world.customer_fp]
    assert pgp.can_encrypt is False
    smime = next(b for b in opts.backends if b.backend == "smime")
    assert {r.address: r.status for r in smime.recipients}[OLD] == "expired"
    assert smime.methods == ["detached"]
    assert len(smime.sign_keys) == 1 and smime.sign_keys[0].usable


def test_compose_options_warn_on_unusable_default(world: CryptoWorld) -> None:
    opts = crypto_options_sync(
        world.config(),
        from_address=SUPPORT,
        recipients=[CUSTOMER],
        default_sign_key="SMIME::Detached::deadbeef.0",
    )
    assert opts.default is None
    assert any("not found" in w for w in opts.warnings)
    off = crypto_options_sync(
        world.config(smime=False),
        from_address=SUPPORT,
        recipients=[],
        default_sign_key="SMIME::Detached::deadbeef.0",
    )
    assert any("disabled" in w for w in off.warnings)
