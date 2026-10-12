"""SCIM provisioning tokens: creation rules, tenant authentication, revocation (both dialects).

Seed band 63080-63099 (actor ids); provider slugs use the ``sc63tok`` prefix.
"""

from __future__ import annotations

import dataclasses
import hmac
from collections.abc import AsyncIterator, Iterator
from datetime import timedelta
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._identity_helpers import make_provider
from tests.test_identity_store import _to_async_url
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraScimEvent, TiqoraScimToken
from tiqora.identity.scim import tokens as tokens_mod
from tiqora.identity.scim.tokens import (
    ScimTokenError,
    authenticate_token,
    create_token,
    hash_token,
    list_tokens,
    revoke_token,
)
from tiqora.identity.store import ProviderChanges, ProviderRecord, update_provider
from tiqora.identity.types import utc_now

pytestmark = pytest.mark.db

KEY = "unit-test-secret-key-for-scim-tokens"
SETTINGS = Settings(environment="test", secret_key=KEY)
ACTOR = 63080
SLUG = "sc63tok"
_URLS: set[str] = set()

_PROVIDERS = "(SELECT id FROM tiqora_identity_provider WHERE slug LIKE 'sc63tok%')"


def _clean(url: str) -> None:
    engine = create_engine(url)
    try:
        TiqoraBase.metadata.create_all(engine)
        with engine.begin() as conn:
            for stmt in (
                f"DELETE FROM tiqora_scim_event WHERE provider_id IN {_PROVIDERS}",
                f"DELETE FROM tiqora_scim_token WHERE provider_id IN {_PROVIDERS}",
                "DELETE FROM tiqora_identity_provider WHERE slug LIKE 'sc63tok%'",
            ):
                conn.execute(text(stmt))
    finally:
        engine.dispose()


@pytest.fixture(autouse=True, scope="module")
def _cleanup_rows() -> Iterator[None]:
    yield
    for url in _URLS:
        _clean(url)
    _URLS.clear()


@pytest_asyncio.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def db(request: pytest.FixtureRequest) -> AsyncIterator[AsyncSession]:
    url: str = request.getfixturevalue(request.param)
    _URLS.add(url)
    _clean(url)
    engine = create_async_engine(_to_async_url(url))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
        await s.rollback()
    await engine.dispose()
    _clean(url)


async def _provider(
    db: AsyncSession, suffix: str, *, audience: Any = "agent", scim: bool = True
) -> ProviderRecord:
    rec = await make_provider(
        db, slug=f"{SLUG}-{suffix}", kind="oidc", audience=audience, settings=SETTINGS
    )
    if scim:
        rec = await update_provider(
            db,
            rec.id,
            ProviderChanges(scim_enabled=True),
            secret_key=KEY,
            actor_id=ACTOR,
            settings=SETTINGS,
        )
    await db.commit()
    return rec


async def _token(
    db: AsyncSession, p: ProviderRecord, kind: Any = "agent", days: int = 365
) -> tuple[Any, str]:
    rec, raw = await create_token(
        db, provider=p, user_kind=kind, name="Entra", expires_in_days=days, actor_id=ACTOR
    )
    await db.commit()
    return rec, raw


async def _last_used(db: AsyncSession, token_id: int) -> Any:
    row = (
        await db.execute(
            text("SELECT last_used_at FROM tiqora_scim_token WHERE id = :i"), {"i": token_id}
        )
    ).first()
    assert row is not None
    return row[0]


@pytest.mark.asyncio
async def test_create_returns_raw_once_and_stores_hash(db: AsyncSession) -> None:
    p = await _provider(db, "create")
    rec, raw = await _token(db, p)
    row = await db.get(TiqoraScimToken, rec.id)
    assert row is not None
    assert raw.startswith("tqscim_") and len(raw) >= 50
    assert row.token_hash == hash_token(raw) and len(row.token_hash) == 64
    assert raw not in (row.token_hash, row.token_hint) and row.token_hint == raw[-4:]
    assert rec.active and rec.expires_at - rec.created_at == timedelta(days=365)
    listed = await list_tokens(db, p.id)
    assert [t.id for t in listed] == [rec.id] and listed[0].token_hint == raw[-4:]
    events = (
        (
            await db.execute(
                text(
                    "SELECT method, resource, operation, actor_user_id, token_id"
                    " FROM tiqora_scim_event WHERE provider_id = :p"
                ),
                {"p": p.id},
            )
        )
        .mappings()
        .all()
    )
    assert len(events) == 1
    assert dict(events[0]) == {
        "method": "ADMIN",
        "resource": "Token",
        "operation": "token_create",
        "actor_user_id": ACTOR,
        "token_id": rec.id,
    }


@pytest.mark.asyncio
async def test_create_rules(db: AsyncSession) -> None:
    agent_p = await _provider(db, "rules-agent", audience="agent")

    async def err(p: ProviderRecord, **kw: Any) -> dict[str, str]:
        args: dict[str, Any] = {
            "provider": p,
            "user_kind": "agent",
            "name": "Entra",
            "expires_in_days": 30,
            "actor_id": ACTOR,
        }
        args.update(kw)
        with pytest.raises(ScimTokenError) as exc:
            await create_token(db, **args)
        return exc.value.errors

    assert "user_kind" in await err(agent_p, user_kind="customer")
    assert "expires_in_days" in await err(agent_p, expires_in_days=0)
    assert "expires_in_days" in await err(agent_p, expires_in_days=731)
    assert "name" in await err(agent_p, name="   ")
    assert "name" in await err(agent_p, name="x" * 121)
    assert "provider" in await err(dataclasses.replace(agent_p, source="env"))
    ldap = await make_provider(
        db, slug=f"{SLUG}-ldap", kind="ldap", enabled=False, settings=SETTINGS
    )
    assert "provider" in await err(ldap)
    for _ in range(5):
        await create_token(
            db, provider=agent_p, user_kind="agent", name="t", expires_in_days=30, actor_id=ACTOR
        )
    assert "tokens" in await err(agent_p)
    # a revoked token frees a slot
    first = (await list_tokens(db, agent_p.id))[0]
    await revoke_token(db, provider_id=agent_p.id, token_id=first.id, actor_id=ACTOR)
    rec, _ = await create_token(
        db, provider=agent_p, user_kind="agent", name="again", expires_in_days=30, actor_id=ACTOR
    )
    assert rec.name == "again"
    await db.rollback()


@pytest.mark.asyncio
async def test_authenticate_happy_path_and_last_used_throttle(db: AsyncSession) -> None:
    p = await _provider(db, "auth")
    rec, raw = await _token(db, p)
    out = await authenticate_token(db, raw, settings=SETTINGS)
    assert out.reason == "ok" and out.tenant is not None
    assert out.tenant.token_id == rec.id and out.tenant.provider.id == p.id
    assert out.tenant.user_kind == "agent"
    assert out.token_id == rec.id and out.provider_id == p.id
    first = await _last_used(db, rec.id)
    assert first is not None
    await authenticate_token(db, raw, settings=SETTINGS)
    assert await _last_used(db, rec.id) == first  # throttled: no second write
    old = utc_now() - timedelta(minutes=5)
    await db.execute(
        text("UPDATE tiqora_scim_token SET last_used_at = :t WHERE id = :i"),
        {"t": old, "i": rec.id},
    )
    await db.commit()
    await authenticate_token(db, raw, settings=SETTINGS)
    assert await _last_used(db, rec.id) > old + timedelta(minutes=4)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ("unknown", "unknown"),
        ("revoked", "revoked"),
        ("expired", "expired"),
        ("provider_disabled", "provider_disabled"),
        ("scim_disabled", "scim_disabled"),
        ("audience_changed", "audience_changed"),
        ("no_prefix", "malformed"),
        ("non_ascii", "malformed"),
        ("too_long", "malformed"),
    ],
)
async def test_authenticate_rejects(db: AsyncSession, case: str, reason: str) -> None:
    p = await _provider(db, f"rej-{case.replace('_', '')}", audience="both")
    rec, raw = await _token(db, p, kind="customer")
    candidate = raw
    if case == "unknown":
        candidate = "tqscim_" + "A" * 43
    elif case == "revoked":
        await revoke_token(db, provider_id=p.id, token_id=rec.id, actor_id=ACTOR)
        await db.commit()
    elif case == "expired":
        await db.execute(
            text("UPDATE tiqora_scim_token SET expires_at = :t WHERE id = :i"),
            {"t": utc_now() - timedelta(seconds=1), "i": rec.id},
        )
        await db.commit()
    elif case == "provider_disabled":
        await db.execute(
            text("UPDATE tiqora_identity_provider SET enabled = :f WHERE id = :i"),
            {"f": False, "i": p.id},
        )
        await db.commit()
    elif case == "scim_disabled":
        await db.execute(
            text("UPDATE tiqora_identity_provider SET scim_enabled = :f WHERE id = :i"),
            {"f": False, "i": p.id},
        )
        await db.commit()
    elif case == "audience_changed":
        await db.execute(
            text("UPDATE tiqora_identity_provider SET audience = 'agent' WHERE id = :i"),
            {"i": p.id},
        )
        await db.commit()
    elif case == "no_prefix":
        candidate = raw[len("tqscim_") :]
    elif case == "non_ascii":
        candidate = "tqscim_ä" + raw[len("tqscim_ä") :]
    elif case == "too_long":
        candidate = raw + "A" * 100
    out = await authenticate_token(db, candidate, settings=SETTINGS)
    assert out.tenant is None
    assert out.reason == reason
    matched = case not in ("unknown", "no_prefix", "non_ascii", "too_long")
    assert out.token_id == (rec.id if matched else None)
    assert out.provider_id == (p.id if matched else None)


@pytest.mark.asyncio
async def test_authenticate_uses_compare_digest(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = await _provider(db, "digest")
    _, raw = await _token(db, p)
    calls: list[tuple[str, str]] = []
    real = hmac.compare_digest

    def spy(a: str, b: str) -> bool:
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(tokens_mod.hmac, "compare_digest", spy)
    out = await authenticate_token(db, raw, settings=SETTINGS)
    assert out.tenant is not None
    assert calls == [(hash_token(raw), hash_token(raw))]


@pytest.mark.asyncio
async def test_revoke_is_idempotent_and_audited(db: AsyncSession) -> None:
    p = await _provider(db, "revoke")
    rec, raw = await _token(db, p)
    first = await revoke_token(db, provider_id=p.id, token_id=rec.id, actor_id=ACTOR)
    await db.commit()
    again = await revoke_token(db, provider_id=p.id, token_id=rec.id, actor_id=ACTOR + 1)
    await db.commit()
    assert first.revoked_at is not None and again.revoked_at is not None
    assert again.revoked_at.replace(microsecond=0) == first.revoked_at.replace(microsecond=0)
    assert not again.active
    ops = (
        await db.execute(
            text(
                "SELECT operation, actor_user_id FROM tiqora_scim_event"
                " WHERE provider_id = :p ORDER BY id"
            ),
            {"p": p.id},
        )
    ).all()
    assert [tuple(r) for r in ops] == [("token_create", ACTOR), ("token_revoke", ACTOR)]
    assert (await authenticate_token(db, raw, settings=SETTINGS)).reason == "revoked"
    other = await _provider(db, "revoke-other")
    with pytest.raises(ScimTokenError):
        await revoke_token(db, provider_id=other.id, token_id=rec.id, actor_id=ACTOR)
    assert await db.get(TiqoraScimEvent, 0) is None
