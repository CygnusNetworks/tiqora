"""Permission engine integration tests (both DB dialects)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.permissions.engine import PermissionEngine

pytestmark = pytest.mark.db

NOW = datetime(2024, 1, 1, 12, 0, 0)


def _seed_permissions(sync_url: str) -> dict[str, Any]:
    """Insert users/groups/roles/queues for permission tests. Returns ids."""
    engine = create_engine(sync_url)
    ids: dict[str, Any] = {}
    with engine.begin() as conn:
        # valid table may already have rows from initial_insert — ensure id=1 exists
        conn.execute(
            text(
                """
                INSERT INTO valid (id, name, create_time, create_by, change_time, change_by)
                VALUES (1, 'valid', :t, 1, :t, 1)
                ON CONFLICT DO NOTHING
                """
                if "postgresql" in sync_url
                else """
                INSERT IGNORE INTO valid (id, name, create_time, create_by, change_time, change_by)
                VALUES (1, 'valid', :t, 1, :t, 1)
                """
            ),
            {"t": NOW},
        )
        # For MySQL without ON CONFLICT — use INSERT IGNORE above.
        # Ensure user root-ish for create_by
        if "postgresql" in sync_url:
            conn.execute(
                text(
                    """
                    INSERT INTO users (id, login, pw, first_name, last_name, valid_id,
                                      create_time, create_by, change_time, change_by)
                    VALUES (1, 'root@localhost', 'x', 'Admin', 'User', 1, :t, 1, :t, 1)
                    ON CONFLICT (id) DO NOTHING
                    """
                ),
                {"t": NOW},
            )
        else:
            conn.execute(
                text(
                    """
                    INSERT IGNORE INTO users
                    (id, login, pw, first_name, last_name, valid_id,
                     create_time, create_by, change_time, change_by)
                    VALUES (1, 'root@localhost', 'x', 'Admin', 'User', 1, :t, 1, :t, 1)
                    """
                ),
                {"t": NOW},
            )

        # Agent with direct perms, agent with role perms, invalid agent
        for uid, login, valid in (
            (100, "agent.direct", 1),
            (101, "agent.role", 1),
            (102, "agent.invalid", 2),
        ):
            conn.execute(
                text(
                    """
                    INSERT INTO users (id, login, pw, first_name, last_name, valid_id,
                                      create_time, create_by, change_time, change_by)
                    VALUES (:id, :login, 'x', 'A', 'B', :v, :t, 1, :t, 1)
                    """
                ),
                {"id": uid, "login": login, "v": valid, "t": NOW},
            )

        for gid, name, valid in (
            (10, "group-alpha", 1),
            (11, "group-beta", 1),
            (12, "group-dead", 2),
        ):
            conn.execute(
                text(
                    """
                    INSERT INTO permission_groups
                    (id, name, valid_id, create_time, create_by, change_time, change_by)
                    VALUES (:id, :name, :v, :t, 1, :t, 1)
                    """
                ),
                {"id": gid, "name": name, "v": valid, "t": NOW},
            )

        # Direct: agent.direct has ro+create on alpha, rw on beta
        for key in ("ro", "create"):
            conn.execute(
                text(
                    """
                    INSERT INTO group_user
                    (user_id, group_id, permission_key,
                     create_time, create_by, change_time, change_by)
                    VALUES (100, 10, :k, :t, 1, :t, 1)
                    """
                ),
                {"k": key, "t": NOW},
            )
        conn.execute(
            text(
                """
                INSERT INTO group_user
                (user_id, group_id, permission_key,
                     create_time, create_by, change_time, change_by)
                VALUES (100, 11, 'rw', :t, 1, :t, 1)
                """
            ),
            {"t": NOW},
        )
        # dead group membership should be ignored
        conn.execute(
            text(
                """
                INSERT INTO group_user
                (user_id, group_id, permission_key,
                     create_time, create_by, change_time, change_by)
                VALUES (100, 12, 'rw', :t, 1, :t, 1)
                """
            ),
            {"t": NOW},
        )

        # Role path
        conn.execute(
            text(
                """
                INSERT INTO roles (id, name, valid_id,
                               create_time, create_by, change_time, change_by)
                VALUES (50, 'agents', 1, :t, 1, :t, 1)
                """
            ),
            {"t": NOW},
        )
        conn.execute(
            text(
                """
                INSERT INTO role_user
                (user_id, role_id, create_time, create_by, change_time, change_by)
                VALUES (101, 50, :t, 1, :t, 1)
                """
            ),
            {"t": NOW},
        )
        for key in ("ro", "move_into", "note"):
            conn.execute(
                text(
                    """
                    INSERT INTO group_role
                    (role_id, group_id, permission_key, permission_value,
                     create_time, create_by, change_time, change_by)
                    VALUES (50, 10, :k, 1, :t, 1, :t, 1)
                    """
                ),
                {"k": key, "t": NOW},
            )
        # permission_value=0 must be ignored
        conn.execute(
            text(
                """
                INSERT INTO group_role
                (role_id, group_id, permission_key, permission_value,
                 create_time, create_by, change_time, change_by)
                VALUES (50, 11, 'owner', 0, :t, 1, :t, 1)
                """
            ),
            {"t": NOW},
        )

        # Minimal queue rows (need system_address etc. — use bare inserts with defaults)
        # Check if we need more tables; queue has NOT NULL FKs without constraints enforced
        for qid, gid, qname in ((1, 10, "Raw"), (2, 11, "Junk")):
            conn.execute(
                text(
                    """
                    INSERT INTO queue (
                        id, name, group_id, system_address_id, salutation_id, signature_id,
                        follow_up_id, follow_up_lock, valid_id,
                        create_time, create_by, change_time, change_by
                    ) VALUES (
                        :id, :name, :gid, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1
                    )
                    """
                ),
                {"id": qid, "name": qname, "gid": gid, "t": NOW},
            )

    engine.dispose()
    ids["direct_user"] = 100
    ids["role_user"] = 101
    ids["invalid_user"] = 102
    ids["queue_alpha"] = 1
    ids["queue_beta"] = 2
    ids["group_alpha"] = 10
    ids["group_beta"] = 11
    return ids


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("mysql+pymysql://"):
        return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    if sync_url.startswith("mysql://"):
        return sync_url.replace("mysql://", "mysql+aiomysql://", 1)
    return sync_url


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_direct_and_role_permissions(
    url_fixture: str,
    request: pytest.FixtureRequest,
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    ids = _seed_permissions(sync_url)
    async_url = _to_async_url(sync_url)
    engine = create_async_engine(async_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        eng = PermissionEngine(session)

        direct = await eng.queue_permissions(ids["direct_user"])
        assert ids["group_alpha"] in direct
        assert direct[ids["group_alpha"]] == {"ro", "create"}
        assert direct[ids["group_beta"]] == {"rw"}
        assert ids.get("group_dead") not in direct
        assert 12 not in direct  # invalid group

        # rw implies all at check time
        assert await eng.check(ids["direct_user"], ids["queue_beta"], "ro")
        assert await eng.check(ids["direct_user"], ids["queue_beta"], "owner")
        assert await eng.check(ids["direct_user"], ids["queue_beta"], "rw")
        assert await eng.check(ids["direct_user"], ids["queue_alpha"], "ro")
        assert await eng.check(ids["direct_user"], ids["queue_alpha"], "create")
        assert not await eng.check(ids["direct_user"], ids["queue_alpha"], "owner")

        role_perms = await eng.queue_permissions(ids["role_user"])
        assert role_perms[ids["group_alpha"]] == {"ro", "move_into", "note"}
        assert ids["group_beta"] not in role_perms  # owner with value 0 ignored
        assert await eng.check(ids["role_user"], ids["queue_alpha"], "move_into")
        assert not await eng.check(ids["role_user"], ids["queue_beta"], "ro")

        # invalid user
        assert await eng.queue_permissions(ids["invalid_user"]) == {}
        assert not await eng.check(ids["invalid_user"], ids["queue_alpha"], "ro")

    await engine.dispose()
