"""Shared helpers for identity tests (wave 1): fake Redis, account seeding, provider factory."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.config import Settings
from tiqora.identity.config_models import AttributeMap
from tiqora.identity.store import ProviderChanges, ProviderRecord, create_provider, update_provider
from tiqora.identity.types import Audience, MatchBy, ProviderKind

NOW = datetime(2026, 10, 8, 12, 0, 0)
_PRESET: dict[str, str] = {"oidc": "generic_oidc", "saml": "generic_saml", "ldap": "ldap"}


class FakeRedis:
    """In-memory subset of redis.asyncio used by identity code (TTL is bookkeeping only)."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}
        self.ttls: dict[str, int] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(
        self, key: str, value: str, ex: int | None = None, nx: bool = False
    ) -> bool | None:
        if nx and key in self.store:
            return None
        self.store[key] = value
        if ex is not None:
            self.ttls[key] = ex
        return True

    async def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            hit = key in self.store or key in self.sets
            self.store.pop(key, None)
            self.sets.pop(key, None)
            self.ttls.pop(key, None)
            removed += int(hit)
        return removed

    async def getdel(self, key: str) -> str | None:
        self.ttls.pop(key, None)
        return self.store.pop(key, None)

    async def expire(self, key: str, seconds: int) -> bool:
        if key in self.store or key in self.sets:
            self.ttls[key] = seconds
            return True
        return False

    async def sadd(self, key: str, *members: str) -> int:
        bucket = self.sets.setdefault(key, set())
        before = len(bucket)
        bucket.update(members)
        return len(bucket) - before

    async def srem(self, key: str, *members: str) -> int:
        bucket = self.sets.get(key, set())
        before = len(bucket)
        bucket.difference_update(members)
        if not bucket:
            self.sets.pop(key, None)
        return before - len(bucket)

    async def smembers(self, key: str) -> set[str]:
        return set(self.sets.get(key, set()))

    async def exists(self, *keys: str) -> int:
        return sum(1 for k in keys if k in self.store or k in self.sets)

    async def ttl(self, key: str) -> int:
        if key not in self.store and key not in self.sets:
            return -2
        return self.ttls.get(key, -1)


async def seed_agent(
    session: AsyncSession,
    user_id: int,
    login: str,
    *,
    email: str | None = None,
    valid_id: int = 1,
) -> None:
    """Insert an agent (and its ``UserEmail`` preference) and commit."""
    await session.execute(
        text(
            "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
            " create_time, create_by, change_time, change_by)"
            " VALUES (:id, :login, 'x', 'Idp', 'Test', :valid, :t, 1, :t, 1)"
        ),
        {"id": user_id, "login": login, "valid": valid_id, "t": NOW},
    )
    if email is not None:
        await session.execute(
            text(
                "INSERT INTO user_preferences (user_id, preferences_key, preferences_value)"
                " VALUES (:id, 'UserEmail', :v)"
            ),
            {"id": user_id, "v": email},
        )
    await session.commit()


async def seed_customer(
    session: AsyncSession,
    login: str,
    *,
    email: str | None = None,
    customer_id: str = "idp-test-co",
    valid_id: int = 1,
) -> int:
    """Insert a customer user and return its id."""
    await session.execute(
        text(
            "INSERT INTO customer_user (login, email, customer_id, first_name, last_name,"
            " valid_id, create_time, create_by, change_time, change_by)"
            " VALUES (:login, :email, :cid, 'Idp', 'Kunde', :valid, :t, 1, :t, 1)"
        ),
        {"login": login, "email": email or login, "cid": customer_id, "valid": valid_id, "t": NOW},
    )
    row = (
        await session.execute(
            text("SELECT id FROM customer_user WHERE login = :login"), {"login": login}
        )
    ).first()
    await session.commit()
    assert row is not None
    return int(row[0])


async def make_provider(
    session: AsyncSession,
    *,
    slug: str,
    kind: ProviderKind = "oidc",
    audience: Audience = "agent",
    match_by: MatchBy = "login",
    attribute_map: AttributeMap | None = None,
    jit_enabled: bool = False,
    require_sso_opt_in: bool = False,
    enabled: bool = True,
    config: dict[str, Any] | None = None,
    settings: Settings,
) -> ProviderRecord:
    """Create a provider from the matching ``generic_*`` preset, then apply the options."""
    created = await create_provider(
        session,
        secret_key=settings.secret_key,
        actor_id=1,
        preset=_PRESET[kind],
        name=f"Test {slug}",
        slug=slug,
        audience=audience,
    )
    return await update_provider(
        session,
        created.id,
        ProviderChanges(
            enabled=enabled,
            match_by=match_by,
            require_sso_opt_in=require_sso_opt_in,
            jit_enabled=jit_enabled,
            attribute_map=attribute_map,
            config=config,
        ),
        secret_key=settings.secret_key,
        actor_id=1,
    )


def cleanup_statements(
    *,
    user_ids: tuple[int, int],
    slug_prefix: str,
    customer_login_like: str = "idp.%@example.org",
) -> tuple[tuple[str, dict[str, Any]], ...]:
    """DELETE statements removing everything a test module seeded (band ``user_ids`` inclusive)."""
    band = {"lo": user_ids[0], "hi": user_ids[1]}
    like = {"p": f"{slug_prefix}%"}
    return (
        (
            "DELETE FROM tiqora_identity_link WHERE provider_id IN"
            " (SELECT id FROM tiqora_identity_provider WHERE slug LIKE :p)",
            like,
        ),
        (
            "DELETE FROM tiqora_identity_managed_assignment WHERE provider_id IN"
            " (SELECT id FROM tiqora_identity_provider WHERE slug LIKE :p)",
            like,
        ),
        ("DELETE FROM tiqora_identity_provider WHERE slug LIKE :p", like),
        ("DELETE FROM tiqora_user_auth_config WHERE user_id BETWEEN :lo AND :hi", band),
        ("DELETE FROM user_preferences WHERE user_id BETWEEN :lo AND :hi", band),
        ("DELETE FROM users WHERE id BETWEEN :lo AND :hi", band),
        ("DELETE FROM customer_user WHERE login LIKE :c", {"c": customer_login_like}),
    )
