"""SSO session info, Redis index and revocation (D8) — pure, no DB."""

from __future__ import annotations

import hashlib

import pytest

from tests._identity_helpers import FakeRedis
from tiqora.config import Settings
from tiqora.domain.auth import SessionStore
from tiqora.domain.customer_auth import CustomerSessionStore
from tiqora.identity.sso_sessions import (
    SsoSessionInfo,
    revoke_sso_sessions,
    sso_key,
    tokens_for,
)


def _info(**kw: object) -> SsoSessionInfo:
    base: dict[str, object] = {
        "provider_id": 7,
        "slug": "idp",
        "kind": "saml",
        "subject_hash": "h1",
    }
    base.update(kw)
    return SsoSessionInfo(**base)  # type: ignore[arg-type]


@pytest.fixture
def redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def settings() -> Settings:
    return Settings(secret_key="unit-test-secret-key")


def test_json_roundtrip_and_garbage() -> None:
    info = _info(session_index="si-a", name_id="n@example.org", sid="abc")
    assert SsoSessionInfo.from_json(info.to_json()) == info
    assert SsoSessionInfo.from_json("garbage") is None
    assert SsoSessionInfo.from_json('{"provider_id": "x"}') is None


def test_sso_key_namespaces() -> None:
    assert sso_key("a", "tok") == "tiqora:session:sso:tok"
    assert sso_key("c", "tok") == "tiqora:csession:sso:tok"


async def test_tokens_for_and_revoke_across_audiences(redis: FakeRedis, settings: Settings) -> None:
    agents = SessionStore(redis, settings)  # type: ignore[arg-type]
    customers = CustomerSessionStore(redis, settings)  # type: ignore[arg-type]
    a = await agents.create(1, "a.one", sso=_info(session_index="si-a"))
    b = await agents.create(2, "a.two", sso=_info(session_index="si-b"))
    c = await customers.create(5, "c.five", sso=_info(kind="saml"))
    other = await agents.create(3, "a.three", sso=_info(subject_hash="h2", session_index="si-z"))
    plain = await agents.create(4, "a.four")

    assert await tokens_for(redis, "a", 7, subject_hash="h1") == {a, b}
    assert await tokens_for(redis, "a", 7, subject_hash="h1", session_indexes=["si-a"]) == {a}
    assert await tokens_for(redis, "a", 8, subject_hash="h1") == set()
    assert await tokens_for(redis, "c", 7, subject_hash="h1") == {c}
    assert (await agents.get_sso(a)) == _info(session_index="si-a")
    assert await agents.get_sso(plain) is None
    assert (await customers.get_sso(c)) == _info()

    assert await revoke_sso_sessions(redis, settings, provider_id=7, subject_hash="h1") == 3
    assert await agents.get(a) is None
    assert await agents.get(b) is None
    assert await customers.get(c) is None
    assert await agents.get(other) is not None
    assert await agents.get(plain) is not None


async def test_sid_index(redis: FakeRedis, settings: Settings) -> None:
    agents = SessionStore(redis, settings)  # type: ignore[arg-type]
    a = await agents.create(1, "a.one", sso=_info(kind="oidc", sid="abc"))
    await agents.create(2, "a.two", sso=_info(kind="oidc", sid="other"))
    assert await tokens_for(redis, "a", 7, sid="abc") == {a}
    assert (await revoke_sso_sessions(redis, settings, provider_id=7, sid="abc")) == 1
    assert await agents.get(a) is None


async def test_index_expires_with_absolute_ttl(redis: FakeRedis, settings: Settings) -> None:
    agents = SessionStore(redis, settings)  # type: ignore[arg-type]
    await agents.create(1, "a.one", sso=_info(sid="abc", session_index="si-a"))
    sha = hashlib.sha256(b"abc").hexdigest()
    idx = f"tiqora:sso:idx:a:7:sid:{sha}"
    assert redis.ttls[idx] == settings.session_absolute_ttl_seconds
    assert redis.ttls["tiqora:sso:idx:a:7:sub:h1"] == settings.session_absolute_ttl_seconds


async def test_delete_drops_sso(redis: FakeRedis, settings: Settings) -> None:
    agents = SessionStore(redis, settings)  # type: ignore[arg-type]
    token = await agents.create(1, "a.one", sso=_info(session_index="si-a"))
    assert f"tiqora:session:sso:{token}" in redis.store
    await agents.delete(token)
    assert f"tiqora:session:sso:{token}" not in redis.store
    assert await tokens_for(redis, "a", 7, subject_hash="h1") == set()


async def test_customer_delete_drops_sso(redis: FakeRedis, settings: Settings) -> None:
    customers = CustomerSessionStore(redis, settings)  # type: ignore[arg-type]
    token = await customers.create(5, "c.five", sso=_info())
    assert f"tiqora:csession:sso:{token}" in redis.store
    await customers.delete(token)
    assert f"tiqora:csession:sso:{token}" not in redis.store


async def test_revoke_customer_sessions(redis: FakeRedis, settings: Settings) -> None:
    customers = CustomerSessionStore(redis, settings)  # type: ignore[arg-type]
    t1 = await customers.create(5, "c.five")
    t2 = await customers.create(5, "c.five")
    t6 = await customers.create(6, "c.six")
    assert await customers.revoke_customer_sessions(5, keep_token=t1) == 1
    assert await customers.get(t1) is not None
    assert await customers.get(t2) is None
    assert await customers.revoke_customer_sessions(5) == 1
    assert await customers.get(t1) is None
    assert await customers.get(t6) is not None
