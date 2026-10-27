"""Core subject type and approver resolvers (A2 ``core_types``). Own rows 970600-970649."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from tests._approvals_helpers import (
    AG1,
    T1,
    T2,
    CapturingMail,
    approvals_session,
    as_client,
    delete_world,
    installed_registry,
    make_fake_subject_type,
)
from tiqora.approvals.contract import (
    APPROVAL_SUBJECT_TYPES,
    APPROVER_RESOLVERS,
    Principal,
    ResolveContext,
)
from tiqora.approvals.core_types import core_resolvers
from tiqora.extensions.core import get_registry, set_registry_for_tests

pytestmark = pytest.mark.db

URLS = ["mariadb_znuny_url", "postgres_znuny_url"]


def _resolver(key: str):  # type: ignore[no-untyped-def]
    return {r.key: r for r in core_resolvers()}[key]


def _ctx(ticket_id: int) -> ResolveContext:
    return ResolveContext(
        ticket_id=ticket_id, subject_type="ticket", subject_ref=None, approval_id=0
    )


def test_core_registry_has_ticket_type_and_resolvers() -> None:
    set_registry_for_tests(None)
    try:
        registry = get_registry()
        ticket = registry.require(APPROVAL_SUBJECT_TYPES, "ticket")
        assert ticket.agent_requestable is True
        assert ticket.allow_customer_approvers is True
        assert ticket.on_decided is None
        customer = registry.require(APPROVER_RESOLVERS, "ticket.customer_user")
        assert customer.yields_customers is True
        assert registry.require(APPROVER_RESOLVERS, "ticket.owner").yields_customers is False
        assert registry.require(APPROVER_RESOLVERS, "ticket.responsible").yields_customers is False
    finally:
        set_registry_for_tests(None)


def test_registry_with_adds_test_items_to_core() -> None:
    fake = make_fake_subject_type("test.subject", allow_customer_approvers=True)
    with installed_registry(fake) as registry:
        assert registry.require(APPROVAL_SUBJECT_TYPES, "test.subject").allow_customer_approvers
        assert registry.get(APPROVAL_SUBJECT_TYPES, "ticket") is not None
        assert get_registry() is registry
    assert get_registry().get(APPROVAL_SUBJECT_TYPES, "test.subject") is None


@pytest.mark.parametrize("url_fixture", URLS)
async def test_resolver_ticket_customer_user(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        got = await _resolver("ticket.customer_user").resolve(session, _ctx(T1))
        assert got == [Principal.customer("appr97-k1")]
        assert await _resolver("ticket.customer_user").resolve(session, _ctx(T2)) == []
        await session.execute(
            text("UPDATE ticket SET customer_user_id = '' WHERE id = :t"), {"t": T1}
        )
        assert await _resolver("ticket.customer_user").resolve(session, _ctx(T1)) == []


@pytest.mark.parametrize("url_fixture", URLS)
async def test_resolver_ticket_owner_and_responsible(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        owner, responsible = _resolver("ticket.owner"), _resolver("ticket.responsible")
        assert await owner.resolve(session, _ctx(T1)) == [Principal.agent(AG1)]
        assert await responsible.resolve(session, _ctx(T1)) == []  # responsible_user_id = 1
        await session.execute(
            text("UPDATE ticket SET responsible_user_id = :u WHERE id = :t"),
            {"u": AG1 + 1, "t": T1},
        )
        assert await responsible.resolve(session, _ctx(T1)) == [Principal.agent(AG1 + 1)]
        assert await owner.resolve(session, _ctx(T2)) == []  # owner is the system user


@pytest.mark.parametrize("url_fixture", URLS)
async def test_resolver_missing_ticket_returns_empty(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture, world=False) as session:
        for resolver in core_resolvers():
            assert await resolver.resolve(session, _ctx(970599)) == []


@pytest.mark.parametrize("url_fixture", URLS)
async def test_seed_world_shape(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        invalid = (
            await session.execute(text("SELECT valid_id FROM users WHERE id = 970106"))
        ).scalar_one()
        assert invalid == 2
        groups = (
            await session.execute(
                text(
                    "SELECT count(*) FROM group_user"
                    " WHERE group_id = 970201 AND permission_key = 'rw'"
                )
            )
        ).scalar_one()
        assert groups == 3
        company = (
            await session.execute(
                text("SELECT customer_id FROM customer_user WHERE email = 'fremd@example.org'")
            )
        ).scalar_one()
        assert company == "appr97-andere"


@pytest.mark.parametrize("url_fixture", URLS)
async def test_delete_world_removes_every_seeded_row(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        await delete_world(session)
        for table, column, values in (
            ("users", "id", "970101, 970106"),
            ("ticket", "id", "970501, 970502"),
            ("queue", "id", "970221, 970222"),
            ("customer_user", "login", "'appr97-k1', 'appr97-fremd'"),
            ("customer_company", "customer_id", "'appr97-firma', 'appr97-andere'"),
            ("role_user", "role_id", "970301"),
            ("group_user", "group_id", "970201, 970211"),
        ):
            sql = f"SELECT count(*) FROM {table} WHERE {column} IN ({values})"
            assert (await session.execute(text(sql))).scalar_one() == 0, table


def test_capturing_mail_replaces_the_outbound_sender(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    from tiqora.domain import mail_outbound

    mail = CapturingMail(monkeypatch)
    sender = asyncio.run(mail_outbound.build_outbound_sender(None))  # type: ignore[arg-type]
    assert sender is mail.sender and mail.sent == []


@pytest.mark.parametrize("url_fixture", URLS)
async def test_as_client_acts_as_the_agent(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        async with await as_client(session, agent=AG1) as client:
            got = await client.get("/api/v1/auth/me")
        assert got.status_code == 200, got.text
        assert got.json()["id"] == AG1
