"""Per-queue email security defaults (tiqora/crypto/queue_security.py).

``decide`` is pure (compose options + policy → offered modes, default,
blocked); ``resolve_mail_security`` is exercised with its loaders patched,
so no gpg/openssl or database is needed.
"""

from __future__ import annotations

from typing import Any

import pytest

from tiqora.crypto import queue_security as qs
from tiqora.crypto.compose import (
    CryptoComposeBackendOut,
    CryptoComposeRecipientOut,
    CryptoOptionsOut,
    EmailSecurityIn,
)

PGP_SIGN = EmailSecurityIn(backend="pgp", method="detached", sign_key="5DF2D6B2")


def _backend(
    name: str, *, keys: dict[str, bool], available: bool = True
) -> CryptoComposeBackendOut:
    rcpts = [
        CryptoComposeRecipientOut(address=a, status="ok" if ok else "missing")
        for a, ok in keys.items()
    ]
    return CryptoComposeBackendOut(
        backend=name,  # type: ignore[arg-type]
        available=available,
        methods=["detached"],
        recipients=rcpts,
        can_encrypt=bool(rcpts) and all(keys.values()),
    )


def _options(
    *backends: CryptoComposeBackendOut, sign: EmailSecurityIn | None = PGP_SIGN
) -> CryptoOptionsOut:
    return CryptoOptionsOut(enabled=True, backends=list(backends), default=sign)


def test_nothing_running_offers_nothing() -> None:
    d = qs.decide(CryptoOptionsOut(enabled=False), qs.QueueSecurityPolicy(encrypt="required"))
    assert d.modes == [] and d.default is None and d.blocked is None
    unusable = _options(_backend("pgp", keys={"a@example.org": True}, available=False))
    assert qs.decide(unusable, qs.QueueSecurityPolicy(encrypt="required")).modes == []


def test_encryption_off_offers_only_none_and_sign() -> None:
    d = qs.decide(_options(_backend("pgp", keys={"a@example.org": True})), qs.QueueSecurityPolicy())
    assert d.modes == ["none", "sign"]
    assert d.default == PGP_SIGN


def test_no_queue_sign_key_hides_sign_modes() -> None:
    opts = _options(_backend("pgp", keys={"a@example.org": True}), sign=None)
    assert qs.decide(opts, qs.QueueSecurityPolicy(encrypt="auto")).modes == ["none", "encrypt"]
    assert qs.decide(opts, qs.QueueSecurityPolicy()).modes == ["none"]


def test_sign_default_off_sends_plain() -> None:
    d = qs.decide(
        _options(_backend("pgp", keys={"a@example.org": True})),
        qs.QueueSecurityPolicy(sign_default=False),
    )
    assert d.modes == ["none", "sign"] and d.default is None


def test_auto_encrypts_when_every_recipient_has_a_key() -> None:
    d = qs.decide(
        _options(_backend("pgp", keys={"a@example.org": True})),
        qs.QueueSecurityPolicy(encrypt="auto"),
    )
    assert d.modes == ["none", "sign", "encrypt", "sign_encrypt"]
    assert d.default is not None and d.default.encrypt and d.default.sign_key == "5DF2D6B2"


def test_auto_without_all_keys_falls_back_to_sign() -> None:
    d = qs.decide(
        _options(_backend("pgp", keys={"a@example.org": True, "b@example.org": False})),
        qs.QueueSecurityPolicy(encrypt="auto"),
    )
    assert d.default == PGP_SIGN and d.blocked is None


def test_required_blocks_without_keys_and_drops_plain_modes() -> None:
    d = qs.decide(
        _options(_backend("pgp", keys={"a@example.org": True, "b@example.org": False})),
        qs.QueueSecurityPolicy(encrypt="required"),
    )
    assert d.modes == ["encrypt", "sign_encrypt"]
    assert d.blocked is not None and "b@example.org" in d.blocked


def test_encryption_switches_to_the_backend_that_has_keys() -> None:
    d = qs.decide(
        _options(
            _backend("pgp", keys={"a@example.org": False}),
            _backend("smime", keys={"a@example.org": True}),
        ),
        qs.QueueSecurityPolicy(encrypt="required"),
    )
    assert d.blocked is None
    assert d.default is not None and d.default.backend == "smime" and d.default.encrypt
    assert d.default.sign_key is None  # the queue key is PGP


def test_policy_round_trip_parsing() -> None:
    assert qs._parse('{"sign_default": false, "encrypt": "required"}') == qs.QueueSecurityPolicy(
        sign_default=False, encrypt="required"
    )
    assert qs._parse('{"encrypt": "bogus"}') == qs.QueueSecurityPolicy()
    assert qs._parse("not json") == qs.QueueSecurityPolicy()


# ------------------------------------------------------------ resolve_mail_security


class _Cfg:
    class _B:
        def __init__(self, enabled: bool) -> None:
            self.enabled = enabled

    def __init__(self, enabled: bool = True) -> None:
        self.pgp, self.smime = self._B(enabled), self._B(False)


@pytest.fixture
def patch(monkeypatch: pytest.MonkeyPatch) -> Any:
    def apply(
        policy: qs.QueueSecurityPolicy, decision: qs.SecurityDecision, enabled: bool = True
    ) -> None:
        async def cfg(_session: Any) -> _Cfg:
            return _Cfg(enabled)

        async def pol(_session: Any, _qid: int) -> qs.QueueSecurityPolicy:
            return policy

        async def dec(_session: Any, **_kw: Any) -> tuple[Any, Any, qs.SecurityDecision]:
            return None, policy, decision

        import tiqora.crypto.config as config_mod

        monkeypatch.setattr(config_mod, "load_crypto_config", cfg)
        monkeypatch.setattr(qs, "load_queue_policy", pol)
        monkeypatch.setattr(qs, "decide_for_mail", dec)

    return apply


async def _resolve(requested: EmailSecurityIn | None, explicit: bool) -> EmailSecurityIn | None:
    return await qs.resolve_mail_security(
        None,  # type: ignore[arg-type]
        queue_id=1,
        from_address="support@example.org",
        recipients=["a@example.org"],
        requested=requested,
        explicit=explicit,
    )


async def test_omitted_choice_takes_queue_default(patch: Any) -> None:
    patch(qs.QueueSecurityPolicy(), qs.SecurityDecision(modes=["none", "sign"], default=PGP_SIGN))
    assert await _resolve(None, explicit=False) == PGP_SIGN


async def test_explicit_choice_wins_unless_required(patch: Any) -> None:
    patch(
        qs.QueueSecurityPolicy(encrypt="auto"),
        qs.SecurityDecision(modes=["none"], default=PGP_SIGN),
    )
    assert await _resolve(None, explicit=True) is None


async def test_required_rejects_explicit_plain_mail(patch: Any) -> None:
    patch(
        qs.QueueSecurityPolicy(encrypt="required"),
        qs.SecurityDecision(
            modes=["encrypt"], default=EmailSecurityIn(backend="pgp", encrypt=True)
        ),
    )
    with pytest.raises(qs.QueueSecurityRequiredError):
        await _resolve(None, explicit=True)
    enc = EmailSecurityIn(backend="pgp", encrypt=True)
    assert await _resolve(enc, explicit=True) == enc


async def test_required_without_keys_blocks_automatic_mail(patch: Any) -> None:
    patch(
        qs.QueueSecurityPolicy(encrypt="required"),
        qs.SecurityDecision(modes=["encrypt"], blocked="no usable key for: a@example.org"),
    )
    with pytest.raises(qs.QueueSecurityRequiredError, match="a@example.org"):
        await _resolve(None, explicit=False)


async def test_disabled_crypto_changes_nothing(patch: Any) -> None:
    patch(qs.QueueSecurityPolicy(encrypt="required"), qs.SecurityDecision(), enabled=False)
    assert await _resolve(None, explicit=False) is None
