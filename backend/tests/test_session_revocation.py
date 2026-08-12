"""Session revocation and the step-up auth marker (security review M-2 / H-2).

Sessions are keyed by an opaque token only, so before the per-user reverse
index there was no way to find — let alone kill — the live sessions of an
account whose password had just been reset.
"""

from __future__ import annotations

import time

import pytest

from tiqora.config import Settings
from tiqora.domain.auth import SessionStore


class _FakeRedis:
    """Minimal async Redis double: string keys, sets, and TTL bookkeeping."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def expire(self, key: str, ttl: int) -> None:
        return None

    async def delete(self, *keys: str) -> None:
        for key in keys:
            self.store.pop(key, None)
            self.sets.pop(key, None)

    async def sadd(self, key: str, *members: str) -> int:
        bucket = self.sets.setdefault(key, set())
        before = len(bucket)
        bucket.update(members)
        return len(bucket) - before

    async def smembers(self, key: str) -> set[str]:
        return set(self.sets.get(key, set()))


@pytest.fixture
def store() -> SessionStore:
    settings = Settings(secret_key="unit-test-secret-key")
    return SessionStore(_FakeRedis(), settings)  # type: ignore[arg-type]


async def test_revoke_kills_every_session_of_that_user(store: SessionStore) -> None:
    a = await store.create(7, "agent.seven")
    b = await store.create(7, "agent.seven")
    other = await store.create(8, "agent.eight")

    assert await store.revoke_user_sessions(7) == 2

    assert await store.get(a) is None
    assert await store.get(b) is None
    # Another account is untouched.
    assert await store.get(other) == (8, "agent.eight")


async def test_revoke_can_spare_the_calling_session(store: SessionStore) -> None:
    """Self-service password change should not log the user out of the tab
    they are standing in."""
    keep = await store.create(7, "agent.seven")
    gone = await store.create(7, "agent.seven")

    assert await store.revoke_user_sessions(7, keep_token=keep) == 1

    assert await store.get(keep) == (7, "agent.seven")
    assert await store.get(gone) is None

    # The index was rewritten, so a second revoke still finds the kept session.
    assert await store.revoke_user_sessions(7) == 1
    assert await store.get(keep) is None


async def test_revoke_is_noop_for_unknown_user(store: SessionStore) -> None:
    assert await store.revoke_user_sessions(4242) == 0


async def test_delete_clears_the_auth_marker(store: SessionStore) -> None:
    token = await store.create(7, "agent.seven")
    assert await store.seconds_since_auth(token) is not None
    await store.delete(token)
    assert await store.seconds_since_auth(token) is None


async def test_seconds_since_auth_reflects_login_not_activity(store: SessionStore) -> None:
    """touch() renews the sliding TTL but must not reset the auth timestamp —
    "recent" has to mean recently *authenticated* for step-up to mean anything."""
    token = await store.create(7, "agent.seven")
    # Backdate the marker by an hour, then simulate continued activity.
    await store._client.set(  # noqa: SLF001 — driving the double directly
        store._auth_at_key(token),  # noqa: SLF001
        str(int(time.time()) - 3600),
    )
    await store.touch(token)

    age = await store.seconds_since_auth(token)
    assert age is not None
    assert age >= 3600


async def test_seconds_since_auth_is_none_for_unknown_token(store: SessionStore) -> None:
    """Sessions created before this marker existed report None; the step-up
    helper treats that as stale rather than trusting it."""
    assert await store.seconds_since_auth("no-such-token") is None


async def test_seconds_since_auth_survives_a_corrupt_marker(store: SessionStore) -> None:
    token = await store.create(7, "agent.seven")
    await store._client.set(store._auth_at_key(token), "not-a-number")  # noqa: SLF001
    assert await store.seconds_since_auth(token) is None
