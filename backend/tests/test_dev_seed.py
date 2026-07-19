"""DB integration smoke test for ``tiqora dev seed`` (backend/src/tiqora/domain/dev_seed.py).

Runs the seeding logic directly (not through the CLI) against the shared
testcontainer Znuny schema with small counts, then asserts customers,
tickets, articles, and history rows exist — the history/outbox invariants
come "for free" because seeding goes through TicketWriteService.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.dev_seed import SeedError, seed_database


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


async def _create_tiqora_tables(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as session, session.begin():
        await session.run_sync(
            lambda sync_session: TiqoraBase.metadata.create_all(sync_session.get_bind())
        )


@pytest.mark.db
@pytest.mark.asyncio
async def test_seed_database_smoke(mariadb_znuny_url: str) -> None:
    url = _mysql_async(mariadb_znuny_url)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await _create_tiqora_tables(factory)

    result = await seed_database(factory, customers=2, tickets=3, seed=42, agent_user_id=1)

    assert result.customers_created == 2
    assert result.customer_users_created >= 2
    assert result.tickets_created == 3
    assert result.articles_created >= 3  # at least one article per ticket

    async with factory() as session:
        n_companies = (
            await session.execute(
                text("SELECT COUNT(*) FROM customer_company WHERE customer_id LIKE 'SEED-%'")
            )
        ).scalar_one()
        assert n_companies == 2

        n_tickets = (
            await session.execute(text("SELECT COUNT(*) FROM ticket WHERE user_id = 1"))
        ).scalar_one()
        assert n_tickets >= 3

        n_history = (
            await session.execute(text("SELECT COUNT(*) FROM ticket_history"))
        ).scalar_one()
        assert n_history >= 3, "NewTicket history rows should exist for every seeded ticket"

        n_outbox = (
            await session.execute(text("SELECT COUNT(*) FROM tiqora_event_outbox"))
        ).scalar_one()
        assert n_outbox >= 3, "TicketCreate outbox events should exist for every seeded ticket"

    await engine.dispose()


@pytest.mark.db
@pytest.mark.asyncio
async def test_seed_can_run_repeatedly_with_different_seeds(mariadb_znuny_url: str) -> None:
    """Repeated invocations (each with a distinct --seed) must not collide on
    uniqueness constraints (per-run nonce on customer_id/login) and must
    reproduce the requested counts each time.

    Note: re-running with the *same* --seed against the *same* database can
    hit real unique constraints such as ``customer_company.name`` (identical
    Faker sequence -> identical company name) — that's a documented caveat
    of full determinism, not covered here. See docs/development.md.
    """
    url = _mysql_async(mariadb_znuny_url)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await _create_tiqora_tables(factory)

    result1 = await seed_database(factory, customers=1, tickets=2, seed=7, agent_user_id=1)
    result2 = await seed_database(factory, customers=1, tickets=2, seed=8, agent_user_id=1)

    assert result1.tickets_created == 2
    assert result2.tickets_created == 2
    assert result1.customers_created == 1
    assert result2.customers_created == 1

    await engine.dispose()


@pytest.mark.db
@pytest.mark.asyncio
async def test_seed_fails_clearly_when_agent_has_no_permissions(mariadb_znuny_url: str) -> None:
    """Seeding with an agent that has no 'create' permission on any queue group
    raises a clear SeedError rather than a raw permission/DB error.

    Uses an unprivileged agent instead of mutating the shared (session-scoped,
    cross-file) testcontainer's queue table, which other tests in the same
    run depend on staying valid.
    """
    url = _mysql_async(mariadb_znuny_url)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await _create_tiqora_tables(factory)

    async with factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (777001, 'seed.unprivileged', 'x', 'No', 'Perms', 1,"
                " current_timestamp, 1, current_timestamp, 1)"
            )
        )

    with pytest.raises(SeedError, match="permission"):
        await seed_database(factory, customers=1, tickets=1, seed=1, agent_user_id=777001)

    await engine.dispose()


def test_require_faker_raises_seed_error_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_require_faker` raises SeedError with a clear message when faker is missing."""
    import builtins

    import tiqora.domain.dev_seed as dev_seed_module

    real_import = builtins.__import__

    def _blocking_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "faker":
            raise ImportError("no module named faker")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", _blocking_import)
    with pytest.raises(SeedError, match="faker"):
        dev_seed_module._require_faker()
