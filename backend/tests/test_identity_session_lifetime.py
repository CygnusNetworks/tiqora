"""Per-provider session lifetime (S10, Task 17).

Pure + FakeRedis tests, plus one both-dialect login test per path. Seed band
63380-63389 (agents ``sc63.agent.<n>``); provider slugs ``idptest-su-*``.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pyotp
import pytest

from tests._identity_helpers import FakeRedis, cleanup_statements
from tests.test_identity_step_up import _record
from tests.test_identity_step_up_login import _URLS, SLUG, Env
from tests.test_identity_step_up_login import env as env_fixture
from tiqora.config import Settings
from tiqora.domain.auth import SessionStore
from tiqora.domain.customer_auth import CustomerSessionStore
from tiqora.identity.session_lifetime import effective_lifetime, idle_from_born
from tiqora.identity.sso_sessions import SsoSessionInfo
from tiqora.identity.store import ProviderChanges, ProviderRecord, update_provider

NOW = datetime(2026, 10, 12, 12, 0, 0, tzinfo=UTC)
BAND = (63380, 63389)
env = env_fixture  # reuse the fake-IdP ASGI environment (both dialects)


def _settings(**kw: int) -> Settings:
    return Settings(
        secret_key="unit-test-secret-key",
        session_ttl_seconds=kw.get("session_ttl_seconds", 3600),
        session_absolute_ttl_seconds=kw.get("session_absolute_ttl_seconds", 43200),
    )


def _rec(idle: int | None, absolute: int | None) -> ProviderRecord:
    return dataclasses.replace(
        _record(kind="oidc", require_mfa_claim=False),
        session_idle_seconds=idle,
        session_absolute_seconds=absolute,
    )


def _info(**kw: object) -> SsoSessionInfo:
    base: dict[str, object] = {
        "provider_id": 7,
        "slug": "idp",
        "kind": "oidc",
        "subject_hash": "h1",
    }
    base.update(kw)
    return SsoSessionInfo(**base)  # type: ignore[arg-type]


# ------------------------------------------------------------------------------- pure


def test_effective_lifetime_caps() -> None:
    s = _settings()
    assert effective_lifetime(_rec(None, None), s, not_on_or_after=None, now=NOW) == (3600, 43200)
    assert effective_lifetime(_rec(900, 7200), s, not_on_or_after=None, now=NOW) == (900, 7200)
    soon = NOW + timedelta(minutes=30)
    assert effective_lifetime(_rec(900, 7200), s, not_on_or_after=soon, now=NOW) == (900, 1800)
    past = NOW - timedelta(hours=1)
    assert effective_lifetime(_rec(None, None), s, not_on_or_after=past, now=NOW) == (60, 60)


def test_effective_lifetime_never_exceeds_global() -> None:
    s = _settings(session_ttl_seconds=600, session_absolute_ttl_seconds=1200)
    assert effective_lifetime(_rec(3600, 86400), s, not_on_or_after=None, now=NOW) == (600, 1200)
    # idle is capped to the (shorter) absolute lifetime
    assert effective_lifetime(_rec(None, 300), s, not_on_or_after=None, now=NOW) == (300, 300)


def test_effective_lifetime_accepts_naive_not_on_or_after() -> None:
    naive = (NOW + timedelta(minutes=10)).replace(tzinfo=None)
    assert effective_lifetime(_rec(None, None), _settings(), not_on_or_after=naive, now=NOW) == (
        600,
        600,
    )


def test_sso_info_json_roundtrip_with_ttls() -> None:
    info = _info(idle_ttl=900, absolute_ttl=7200)
    back = SsoSessionInfo.from_json(info.to_json())
    assert back == info and (back.idle_ttl, back.absolute_ttl) == (900, 7200)
    legacy = json.loads(_info().to_json())
    legacy.pop("idle_ttl")
    legacy.pop("absolute_ttl")
    parsed = SsoSessionInfo.from_json(json.dumps(legacy))
    assert parsed is not None and (parsed.idle_ttl, parsed.absolute_ttl) == (None, None)
    for bad in (True, "900", 0, -5, 1.5):
        raw = json.loads(info.to_json())
        raw["idle_ttl"] = bad
        assert SsoSessionInfo.from_json(json.dumps(raw)) is None, bad
    raw = json.loads(info.to_json())
    raw["absolute_ttl"] = "7200"
    assert SsoSessionInfo.from_json(json.dumps(raw)) is None


def test_idle_from_born() -> None:
    assert idle_from_born("i=900", 3600) == 900
    assert idle_from_born(b"i=900", 3600) == 900
    assert idle_from_born("1", 3600) == 3600  # legacy marker
    assert idle_from_born(None, 3600) == 3600
    assert idle_from_born("i=59", 3600) == 3600  # below the floor
    assert idle_from_born("i=abc", 3600) == 3600
    assert idle_from_born("i=99999", 3600) == 3600  # never longer than the global TTL


# --------------------------------------------------------------------------- stores


async def test_create_uses_sso_ttls() -> None:
    redis = FakeRedis()
    store = SessionStore(redis, _settings())  # type: ignore[arg-type]
    tok = await store.create(5, "sc63", sso=_info(idle_ttl=900, absolute_ttl=7200))
    assert redis.ttls[f"tiqora:session:{tok}"] == 900
    assert redis.ttls[f"tiqora:session:born:{tok}"] == 7200
    assert redis.store[f"tiqora:session:born:{tok}"] == "i=900"
    assert redis.ttls[f"tiqora:session:authat:{tok}"] == 7200
    assert redis.ttls[f"tiqora:session:sso:{tok}"] == 7200
    assert await store.get(tok) == (5, "sc63")


async def test_create_without_sso_uses_global_ttls() -> None:
    redis = FakeRedis()
    store = SessionStore(redis, _settings())  # type: ignore[arg-type]
    tok = await store.create(5, "sc63")
    assert redis.ttls[f"tiqora:session:{tok}"] == 3600
    assert redis.ttls[f"tiqora:session:born:{tok}"] == 43200
    assert redis.store[f"tiqora:session:born:{tok}"] == "i=3600"


async def test_stored_ttls_longer_than_global_are_clamped() -> None:
    redis = FakeRedis()
    store = SessionStore(redis, _settings())  # type: ignore[arg-type]
    tok = await store.create(5, "sc63", sso=_info(idle_ttl=99999, absolute_ttl=999999))
    assert redis.ttls[f"tiqora:session:{tok}"] == 3600
    assert redis.ttls[f"tiqora:session:born:{tok}"] == 43200


async def test_touch_keeps_provider_idle_ttl() -> None:
    redis = FakeRedis()
    store = SessionStore(redis, _settings())  # type: ignore[arg-type]
    tok = await store.create(
        5,
        "sc63",
        avatar_url="https://example.org/a.png",
        sso=_info(idle_ttl=900, absolute_ttl=7200),
    )
    redis.ttls[f"tiqora:session:{tok}"] = 17
    redis.ttls[f"tiqora:session:avatar:{tok}"] = 17
    await store.touch(tok)
    assert redis.ttls[f"tiqora:session:{tok}"] == 900  # not the global 3600
    assert redis.ttls[f"tiqora:session:avatar:{tok}"] == 900
    assert redis.ttls[f"tiqora:session:born:{tok}"] == 7200  # born marker is never renewed


async def test_legacy_born_value_uses_global() -> None:
    redis = FakeRedis()
    store = SessionStore(redis, _settings())  # type: ignore[arg-type]
    tok = await store.create(5, "sc63", sso=_info(idle_ttl=900, absolute_ttl=7200))
    redis.store[f"tiqora:session:born:{tok}"] = "1"  # session created before the upgrade
    await store.touch(tok)
    assert redis.ttls[f"tiqora:session:{tok}"] == 3600
    assert await store.get(tok) == (5, "sc63")  # any non-empty born value is alive
    redis.store[f"tiqora:session:born:{tok}"] = "i=garbage"
    await store.touch(tok)
    assert redis.ttls[f"tiqora:session:{tok}"] == 3600


async def test_customer_store_same() -> None:
    redis = FakeRedis()
    store = CustomerSessionStore(redis, _settings())  # type: ignore[arg-type]
    tok = await store.create(9, "kunde@example.org", sso=_info(idle_ttl=900, absolute_ttl=7200))
    assert redis.ttls[store._key(tok)] == 900
    assert redis.ttls[store._born_key(tok)] == 7200
    assert redis.store[store._born_key(tok)] == "i=900"
    redis.ttls[store._key(tok)] = 5
    await store.touch(tok)
    assert redis.ttls[store._key(tok)] == 900
    redis.store[store._born_key(tok)] = "1"
    await store.touch(tok)
    assert redis.ttls[store._key(tok)] == 3600
    plain = await store.create(9, "kunde@example.org")
    assert redis.ttls[store._key(plain)] == 3600


# ----------------------------------------------------------------------- login (DB)


@pytest.fixture(autouse=True, scope="module")
def _cleanup_band() -> Iterator[None]:
    yield
    from sqlalchemy import create_engine, text

    for url in set(_URLS):
        engine = create_engine(url)
        try:
            with engine.begin() as conn:
                for stmt, params in (
                    (
                        "DELETE FROM tiqora_user_totp WHERE user_id BETWEEN :lo AND :hi",
                        {"lo": BAND[0], "hi": BAND[1]},
                    ),
                    (
                        "DELETE FROM group_user WHERE user_id BETWEEN :lo AND :hi",
                        {"lo": BAND[0], "hi": BAND[1]},
                    ),
                    *cleanup_statements(
                        user_ids=BAND,
                        slug_prefix=SLUG,
                        customer_login_like="sc63.kunde.%@example.org",
                    ),
                ):
                    conn.execute(text(stmt), params)
        finally:
            engine.dispose()


async def _set_lifetime(env: Env, provider_id: int, idle: int, absolute: int) -> None:
    await update_provider(
        env.session,
        provider_id,
        ProviderChanges(session_idle_seconds=idle, session_absolute_seconds=absolute),
        secret_key="unit-test-secret-key-for-identity-step-up-login",
        actor_id=1,
        settings=env.settings,
    )
    await env.session.commit()


async def test_sso_login_applies_provider_lifetime(env: Env) -> None:
    login = await env.agent(63380)
    rec = await env.provider("life", mfa=False)
    await _set_lifetime(env, rec.id, 900, 7200)
    resp = await env.login("life", {"sub": "s-63380", "preferred_username": login})
    assert resp.status_code == 302
    token = env.client.cookies.get("tiqora_session")
    assert token
    assert env.redis.ttls[f"tiqora:session:{token}"] <= 900
    assert env.redis.ttls[f"tiqora:session:born:{token}"] <= 7200
    assert env.redis.store[f"tiqora:session:born:{token}"] == "i=900"
    info = await env.agent_store.get_sso(token)
    assert info is not None and (info.idle_ttl, info.absolute_ttl) == (900, 7200)
    assert (await env.client.get("/api/v1/auth/me")).status_code == 200
    # the sliding renewal on /me keeps the provider's idle TTL
    assert env.redis.ttls[f"tiqora:session:{token}"] == 900


async def test_promoted_step_up_session_keeps_lifetime(env: Env) -> None:
    login = await env.agent(63381)
    secret = await env.enroll_totp(63381, login)
    rec = await env.provider("lifeup")
    await _set_lifetime(env, rec.id, 900, 7200)
    await env.login("lifeup", {"sub": "s-63381", "preferred_username": login}, amr=["pwd"])
    pending = env.client.cookies.get("tiqora_session")
    assert pending and await env.agent_store.get_pending(pending) == (63381, login)
    verify = await env.client.post(
        "/api/v1/auth/totp/verify", json={"code": pyotp.TOTP(secret).now()}
    )
    assert verify.status_code == 200, verify.text
    full = env.client.cookies.get("tiqora_session")
    assert full and full != pending
    assert env.redis.ttls[f"tiqora:session:{full}"] <= 900
    assert env.redis.ttls[f"tiqora:session:born:{full}"] <= 7200
    assert env.redis.store[f"tiqora:session:born:{full}"] == "i=900"
    info = await env.agent_store.get_sso(full)
    assert info is not None and (info.idle_ttl, info.absolute_ttl) == (900, 7200)
