"""Unit-level DB tests for ``tiqora.domain.customer_link.resolve_customer_link``.

Exercises the resolver directly against ``tiqora_queue_customer_link`` +
``customer_user`` rows (no ticket/permission scaffolding needed — the
resolver takes plain scalars, not an ORM ticket), covering: all 5 template
placeholders, URL-encoding, the ``#`` login-suffix strip, admin vs.
non-admin template choice, ``visibility="admins"``, and "no config row".
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraQueueCustomerLink
from tiqora.domain.customer_link import resolve_customer_link

pytestmark = pytest.mark.db

NOW = datetime(2024, 6, 1, 12, 0, 0)


def _to_async_url(sync_url: str) -> str:
    for old, new in (
        ("postgresql+psycopg2://", "postgresql+asyncpg://"),
        ("postgresql://", "postgresql+asyncpg://"),
        ("mysql+pymysql://", "mysql+aiomysql://"),
        ("mysql://", "mysql+aiomysql://"),
    ):
        if sync_url.startswith(old):
            return sync_url.replace(old, new, 1)
    return sync_url


async def _make_session(sync_url: str) -> tuple[AsyncSession, object]:
    engine_sync = create_engine(sync_url)
    with engine_sync.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
    engine_sync.dispose()

    async_url = _to_async_url(sync_url)
    engine = create_async_engine(async_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return factory(), engine


def _seed_customer_user(sync_url: str, *, login: str, ns: int) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM customer_user WHERE login = :l"), {"l": login})
        conn.execute(
            text(
                """
                INSERT INTO customer_user (login, email, customer_id, pw,
                    first_name, last_name, valid_id, create_time, create_by,
                    change_time, change_by)
                VALUES (:login, :email, :cid, 'x', 'Erika', 'Musterfrau', 1,
                    :t, 1, :t, 1)
                """
            ),
            {"login": login, "email": f"erika{ns}@example.com", "cid": f"CUST{ns}", "t": NOW},
        )
    engine.dispose()


def _delete_customer_user(sync_url: str, *, login: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM customer_user WHERE login = :l"), {"l": login})
    engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_resolve_customer_link_placeholders_and_encoding(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    ns = uuid.uuid4().int % 100_000
    queue_id = 87000 + (ns % 100)
    login = f"z{ns}test"
    # The customer_user row keeps the FULL Znuny login (incl. "#3") — only
    # the {customer_user} placeholder is stripped via login_suffix_separator.
    _seed_customer_user(sync_url, login=f"{login}#3", ns=ns)

    session, engine = await _make_session(sync_url)
    async with session as s:
        s.add(
            TiqoraQueueCustomerLink(
                queue_id=queue_id,
                url_template=(
                    "https://netadmin.example/diagnosis"
                    "?portid={customer_user}&userid={customer_id}"
                    "&tn={ticket_number}&email={customer_email}&name={customer_name}"
                ),
                admin_url_template=None,
                label="Diagnose",
                login_suffix_separator="#",
                visibility="all",
                create_by=1,
                change_by=1,
            )
        )
        await s.commit()

        resolved = await resolve_customer_link(
            s,
            queue_id=queue_id,
            ticket_number="20240601000123",
            customer_id=f"CUST{ns}",
            customer_user_id=f"{login}#3",
            is_admin=False,
        )
        assert resolved.label == "Diagnose"
        assert resolved.url is not None
        assert f"portid={login}" in resolved.url
        assert f"userid=CUST{ns}" in resolved.url
        assert "tn=20240601000123" in resolved.url
        assert f"email=erika{ns}%40example.com" in resolved.url
        assert "name=Erika%20Musterfrau" in resolved.url
        # The '#3' Znuny disambiguator must never leak into the URL.
        assert "#3" not in resolved.url
        assert "%233" not in resolved.url

        await s.execute(
            text("DELETE FROM tiqora_queue_customer_link WHERE queue_id = :q"),
            {"q": queue_id},
        )
        await s.commit()

    _delete_customer_user(sync_url, login=f"{login}#3")
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_resolve_customer_link_admin_vs_visibility(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    ns = uuid.uuid4().int % 100_000
    queue_open = 87100 + (ns % 50)
    queue_admin_only = 87150 + (ns % 50)

    session, engine = await _make_session(sync_url)
    async with session as s:
        s.add_all(
            [
                TiqoraQueueCustomerLink(
                    queue_id=queue_open,
                    url_template="https://normal.example/?u={customer_user}",
                    admin_url_template="https://admin-krb.example/?u={customer_user}",
                    label=None,
                    visibility="all",
                    create_by=1,
                    change_by=1,
                ),
                TiqoraQueueCustomerLink(
                    queue_id=queue_admin_only,
                    url_template="https://normal.example/?u={customer_user}",
                    admin_url_template=None,
                    label=None,
                    visibility="admins",
                    create_by=1,
                    change_by=1,
                ),
            ]
        )
        await s.commit()

        # Non-admin on the "all" queue: normal template, no default admin_url leak.
        as_agent = await resolve_customer_link(
            s,
            queue_id=queue_open,
            ticket_number="tn",
            customer_id=None,
            customer_user_id="u1",
            is_admin=False,
        )
        assert as_agent.url == "https://normal.example/?u=u1"

        # Admin on the same queue: admin_url_template wins.
        as_admin = await resolve_customer_link(
            s,
            queue_id=queue_open,
            ticket_number="tn",
            customer_id=None,
            customer_user_id="u1",
            is_admin=True,
        )
        assert as_admin.url == "https://admin-krb.example/?u=u1"

        # visibility="admins": non-admin gets nothing at all.
        hidden = await resolve_customer_link(
            s,
            queue_id=queue_admin_only,
            ticket_number="tn",
            customer_id=None,
            customer_user_id="u1",
            is_admin=False,
        )
        assert hidden.url is None
        assert hidden.label is None

        # Admin without an admin_url_template falls back to the normal one.
        admin_fallback = await resolve_customer_link(
            s,
            queue_id=queue_admin_only,
            ticket_number="tn",
            customer_id=None,
            customer_user_id="u1",
            is_admin=True,
        )
        assert admin_fallback.url == "https://normal.example/?u=u1"

        await s.execute(
            text("DELETE FROM tiqora_queue_customer_link WHERE queue_id IN (:q1, :q2)"),
            {"q1": queue_open, "q2": queue_admin_only},
        )
        await s.commit()

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_resolve_customer_link_no_config_row_is_null(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    ns = uuid.uuid4().int % 100_000
    queue_id = 87050 + (ns % 50)

    session, engine = await _make_session(sync_url)
    async with session as s:
        resolved = await resolve_customer_link(
            s,
            queue_id=queue_id,
            ticket_number="tn",
            customer_id=None,
            customer_user_id=None,
            is_admin=False,
        )
        assert resolved.url is None
        assert resolved.label is None

    await engine.dispose()


def test_strip_login_suffix_is_config_driven() -> None:
    """No separator configured = login verbatim (the '#' rule is a
    site-specific convention, not a built-in)."""
    from tiqora.domain.customer_link import _strip_login_suffix

    assert _strip_login_suffix("z50test#3", None) == "z50test#3"
    assert _strip_login_suffix("z50test#3", "") == "z50test#3"
    assert _strip_login_suffix("z50test#3", "#") == "z50test"
    assert _strip_login_suffix("a-b-c", "-") == "a"
