"""Admin CRUD for PostMaster filters (postmaster_filter composite-PK table).

Direct-service-call pattern (same as test_admin_api.py): seed against the
shared Znuny testcontainer, then exercise router functions on a real async
session. Covers create (2 Match + 1 Set), GET grouping, PUT replace/rename,
DELETE, and POST duplicate-name → 409.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.api.v1.admin import postmaster_filters as admin_pm
from tiqora.api.v1.admin.schemas import (
    PostmasterFilterWrite,
    PostmasterMatchRuleIn,
    PostmasterSetRuleIn,
)
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.auth import AuthenticatedUser

pytestmark = pytest.mark.db

NOW = datetime(2024, 6, 1, 12, 0, 0)


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("mysql+pymysql://"):
        return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    return sync_url


def _seed_admin(sync_url: str) -> dict[str, int]:
    """Root (id=1) is present via Znuny initial_insert; return its id."""
    _ = sync_url
    return {"admin_id": 1}


async def _make_session(sync_url: str) -> tuple[AsyncSession, object]:
    async_url = _to_async_url(sync_url)
    engine = create_async_engine(async_url)
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: TiqoraBase.metadata.create_all(c, checkfirst=True))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return factory(), engine


def _clean_filter(sync_url: str, name: str) -> None:
    """Idempotent seed cleanup for the composite-PK table."""
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM postmaster_filter WHERE f_name = :n"),
            {"n": name},
        )
    engine.dispose()


def _admin_user(admin_id: int = 1) -> AuthenticatedUser:
    return AuthenticatedUser(
        id=admin_id,
        login="root@localhost",
        first_name="Admin",
        last_name="Znuny",
        auth_method="session",
    )


def _write_body(
    name: str,
    *,
    stop: bool = False,
    matches: list[tuple[str, str, bool]] | None = None,
    sets: list[tuple[str, str]] | None = None,
) -> PostmasterFilterWrite:
    if matches is None:
        matches = [("From", "spam@example.com", False), ("Subject", r"^ADV:", True)]
    if sets is None:
        sets = [("X-OTRS-Ignore", "yes")]
    return PostmasterFilterWrite(
        name=name,
        stop=stop,
        match=[PostmasterMatchRuleIn(key=k, value=v, negate=neg) for k, v, neg in matches],
        set=[PostmasterSetRuleIn(key=k, value=v) for k, v in sets],
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_admin_postmaster_filter_crud_roundtrip(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    ids = _seed_admin(sync_url)
    ns = uuid.uuid4().hex[:8]
    name = f"tiqora-pm-filter-{ns}"
    renamed = f"tiqora-pm-filter-renamed-{ns}"
    _clean_filter(sync_url, name)
    _clean_filter(sync_url, renamed)

    session, engine = await _make_session(sync_url)
    admin = _admin_user(ids["admin_id"])

    async with session as s:
        created = await admin_pm.create_postmaster_filter(
            _write_body(name, stop=True),
            admin,
            s,
        )
        assert created.name == name
        assert len(created.rules) == 3
        types = {r.f_type for r in created.rules}
        assert types == {"Match", "Set"}
        assert all(r.f_stop == 1 for r in created.rules)
        match_rules = [r for r in created.rules if r.f_type == "Match"]
        set_rules = [r for r in created.rules if r.f_type == "Set"]
        assert len(match_rules) == 2
        assert len(set_rules) == 1
        assert set_rules[0].f_key == "X-OTRS-Ignore"
        assert set_rules[0].f_value == "yes"
        # One Match has negate=True → f_not=1
        assert any(r.f_not == 1 for r in match_rules)
        assert any(r.f_not == 0 for r in match_rules)

        got = await admin_pm.get_postmaster_filter(name, admin, s)
        assert got.name == name
        assert len(got.rules) == 3

        listed = await admin_pm.list_postmaster_filters(admin, s)
        names = {f.name for f in listed}
        assert name in names

        # PUT replace: drop one Match, change Set, keep stop=False
        updated = await admin_pm.update_postmaster_filter(
            name,
            _write_body(
                name,
                stop=False,
                matches=[("Subject", r"^Newsletter", False)],
                sets=[("X-OTRS-Queue", "Raw")],
            ),
            admin,
            s,
        )
        assert updated.name == name
        assert len(updated.rules) == 2
        assert all(int(r.f_stop or 0) == 0 for r in updated.rules)
        assert {r.f_type for r in updated.rules} == {"Match", "Set"}
        assert any(r.f_key == "X-OTRS-Queue" and r.f_value == "Raw" for r in updated.rules)
        assert not any(r.f_key == "X-OTRS-Ignore" for r in updated.rules)

        # PUT rename
        renamed_out = await admin_pm.update_postmaster_filter(
            name,
            _write_body(
                renamed,
                stop=True,
                matches=[("From", "news@example.com", False)],
                sets=[("X-OTRS-Priority", "3 normal")],
            ),
            admin,
            s,
        )
        assert renamed_out.name == renamed
        with pytest.raises(HTTPException) as not_found:
            await admin_pm.get_postmaster_filter(name, admin, s)
        assert not_found.value.status_code == 404
        still = await admin_pm.get_postmaster_filter(renamed, admin, s)
        assert still.name == renamed
        assert len(still.rules) == 2

        # DELETE removes all rows for the name
        await admin_pm.delete_postmaster_filter(renamed, admin, s)
        with pytest.raises(HTTPException) as gone:
            await admin_pm.get_postmaster_filter(renamed, admin, s)
        assert gone.value.status_code == 404

        # Direct table assertion — zero rows left under either name
        for n in (name, renamed):
            count = (
                await s.execute(
                    text("SELECT COUNT(*) FROM postmaster_filter WHERE f_name = :n"),
                    {"n": n},
                )
            ).scalar_one()
            assert count == 0

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_admin_postmaster_filter_duplicate_name_409(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    ids = _seed_admin(sync_url)
    ns = uuid.uuid4().hex[:8]
    name = f"tiqora-pm-dup-{ns}"
    _clean_filter(sync_url, name)

    session, engine = await _make_session(sync_url)
    admin = _admin_user(ids["admin_id"])

    async with session as s:
        await admin_pm.create_postmaster_filter(_write_body(name), admin, s)
        with pytest.raises(HTTPException) as exc:
            await admin_pm.create_postmaster_filter(_write_body(name), admin, s)
        assert exc.value.status_code == 409

        # cleanup
        await admin_pm.delete_postmaster_filter(name, admin, s)

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_admin_postmaster_filter_requires_match(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """Pydantic rejects empty match list before the handler runs."""
    _ = request.getfixturevalue(url_fixture)
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        PostmasterFilterWrite(
            name="no-match",
            stop=False,
            match=[],
            set=[PostmasterSetRuleIn(key="X-OTRS-Ignore", value="yes")],
        )
