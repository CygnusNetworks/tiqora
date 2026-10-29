"""Shared pieces of the approvals tests (plan band 970000-971999).

970100-970599 is the helper world seeded by :func:`seed_world`; every task owns its own
sub-band above it. ``approvals_session`` hands out a rolled-back session (nothing it seeds is
committed); tests that need real commits (two sessions racing) seed through ``seed_world`` on a
committing session and call :func:`delete_world` in their teardown.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel, ConfigDict
from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tests._itsm_adapter_helpers import seed_company, seed_customer, seed_user
from tiqora.approvals import models as _approval_models  # noqa: F401  (registers the tables)
from tiqora.approvals.contract import (
    APPROVAL_SUBJECT_TYPES,
    APPROVER_RESOLVERS,
    JOB_HOOK,
    JOB_MAIL,
    ApprovalOutcome,
    ApprovalSubjectType,
    ApproverResolver,
)
from tiqora.channels.email.smtp import CapturingMailSender
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.auth import AuthenticatedUser
from tiqora.domain.customer_auth import AuthenticatedCustomer
from tiqora.extensions import core as extensions_core
from tiqora.extensions.core import register_core, set_registry_for_tests
from tiqora.extensions.registry import Registry
from tiqora.jobs.registry import JOB_HANDLERS, JobHandler

NOW = datetime(2026, 10, 15, 8, 0, 0)  # noqa: DTZ001 - Znuny stores naive UTC

AG1, AG2, AG3, AG4, AG5, AG6, AG7 = 970101, 970102, 970103, 970104, 970105, 970106, 970107
GROUP_QA, GROUP_QB, GROUP_CAB = 970211, 970212, 970201
ROLE = 970301
QUEUE_A, QUEUE_B = 970221, 970222
T1, T2 = 970501, 970502
CUSTOMER_1, CUSTOMER_2, CUSTOMER_OTHER = (
    "kunde1@example.org",
    "kunde2@example.org",
    "fremd@example.org",
)
FIRMA, ANDERE = "appr97-firma", "appr97-andere"
_AGENTS = {
    AG1: "appr97-agent1",
    AG2: "appr97-agent2",
    AG3: "appr97-agent3",
    AG4: "appr97-agent4",
    AG5: "appr97-agent5",
    AG6: "appr97-agent6",
    AG7: "appr97-agent7",
}
# login -> (email, company)
_CUSTOMERS = {
    "appr97-k1": (CUSTOMER_1, FIRMA),
    "appr97-k2": (CUSTOMER_2, FIRMA),
    "appr97-fremd": (CUSTOMER_OTHER, ANDERE),
}


@dataclass(frozen=True)
class World:
    ag1: int = AG1
    ag2: int = AG2
    ag3: int = AG3
    ag4: int = AG4
    ag5: int = AG5
    ag6: int = AG6
    ag7: int = AG7
    queue_a: int = QUEUE_A
    queue_b: int = QUEUE_B
    t1: int = T1
    t2: int = T2
    firma: str = FIRMA
    andere: str = ANDERE
    group_cab: int = GROUP_CAB
    role: int = ROLE
    customers: dict[str, tuple[str, str]] = field(default_factory=lambda: dict(_CUSTOMERS))


async def _grant(session: AsyncSession, user_id: int, group_id: int, key: str) -> None:
    await session.execute(
        text(
            "INSERT INTO group_user (user_id, group_id, permission_key, create_time, create_by,"
            " change_time, change_by) VALUES (:u, :g, :k, :t, 1, :t, 1)"
        ),
        {"u": user_id, "g": group_id, "k": key, "t": NOW},
    )


async def seed_world(session: AsyncSession) -> World:
    """Flushes the helper world into *session* (the caller decides to commit or roll back).

    CAB group ``appr97-cab`` (970201): AG3, AG4, AG5 hold ``rw`` directly, AG4 also through role
    ``appr97-role`` (``group_role`` rw), and AG7 is an ``ro``-only member (not an approver).
    """
    for uid, login in _AGENTS.items():
        await seed_user(session, uid, login, "Appr", login)
    await session.execute(text("UPDATE users SET valid_id = 2 WHERE id = :u"), {"u": AG6})
    for gid, name in (
        (GROUP_QA, "appr97-ga"),
        (GROUP_QB, "appr97-gb"),
        (GROUP_CAB, "appr97-cab"),
    ):
        await session.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id, create_time, create_by,"
                " change_time, change_by) VALUES (:id, :n, 1, :t, 1, :t, 1)"
            ),
            {"id": gid, "n": name, "t": NOW},
        )
    await _grant(session, AG1, GROUP_QA, "rw")
    await _grant(session, AG2, GROUP_QA, "ro")
    for uid in (AG3, AG4, AG5):
        await _grant(session, uid, GROUP_CAB, "rw")
    await _grant(session, AG7, GROUP_CAB, "ro")
    await session.execute(
        text(
            "INSERT INTO roles (id, name, valid_id, create_time, create_by, change_time,"
            " change_by) VALUES (:id, 'appr97-role', 1, :t, 1, :t, 1)"
        ),
        {"id": ROLE, "t": NOW},
    )
    await session.execute(
        text(
            "INSERT INTO role_user (user_id, role_id, create_time, create_by, change_time,"
            " change_by) VALUES (:u, :r, :t, 1, :t, 1)"
        ),
        {"u": AG4, "r": ROLE, "t": NOW},
    )
    await session.execute(
        text(
            "INSERT INTO group_role (role_id, group_id, permission_key, permission_value,"
            " create_time, create_by, change_time, change_by)"
            " VALUES (:r, :g, 'rw', 1, :t, 1, :t, 1)"
        ),
        {"r": ROLE, "g": GROUP_CAB, "t": NOW},
    )
    for qid, gid, name in ((QUEUE_A, GROUP_QA, "appr97-qa"), (QUEUE_B, GROUP_QB, "appr97-qb")):
        await session.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id, create_time, create_by,"
                " change_time, change_by) VALUES (:id, :n, :g, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
            ),
            {"id": qid, "n": name, "g": gid, "t": NOW},
        )
    for company in (FIRMA, ANDERE):
        await seed_company(session, company, company)
    for login, (email, company) in _CUSTOMERS.items():
        await seed_customer(session, login, "Kunde", login, company)
        await session.execute(
            text("UPDATE customer_user SET email = :e WHERE login = :l"),
            {"e": email, "l": login},
        )
    for tid, qid, customer, company in (
        (T1, QUEUE_A, "appr97-k1", FIRMA),
        (T2, QUEUE_B, None, None),
    ):
        await session.execute(
            text(
                "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id, user_id,"
                " responsible_user_id, ticket_priority_id, ticket_state_id, customer_id,"
                " customer_user_id, timeout, until_time, escalation_time,"
                " escalation_update_time, escalation_response_time, escalation_solution_time,"
                " archive_flag, create_time, create_by, change_time, change_by)"
                " VALUES (:id, :tn, 'appr97 ticket', :q, 1, 1, :owner, 1, 3, 1, :cid, :cu,"
                " 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
            ),
            {
                "id": tid,
                "tn": f"20261015{tid}",
                "q": qid,
                "owner": AG1 if tid == T1 else 1,
                "cid": company,
                "cu": customer,
                "t": NOW,
            },
        )
    await session.flush()
    return World()


async def delete_world(session: AsyncSession) -> None:
    """Removes every row :func:`seed_world` wrote (for tests that committed the world)."""
    users = tuple(_AGENTS)
    statements: list[tuple[str, dict[str, Any]]] = [
        ("DELETE FROM ticket WHERE id IN :ids", {"ids": (T1, T2)}),
        ("DELETE FROM queue WHERE id IN :ids", {"ids": (QUEUE_A, QUEUE_B)}),
        ("DELETE FROM group_role WHERE role_id IN :ids", {"ids": (ROLE,)}),
        ("DELETE FROM role_user WHERE role_id IN :ids", {"ids": (ROLE,)}),
        ("DELETE FROM roles WHERE id IN :ids", {"ids": (ROLE,)}),
        ("DELETE FROM group_user WHERE user_id IN :ids", {"ids": users}),
        (
            "DELETE FROM permission_groups WHERE id IN :ids",
            {"ids": (GROUP_QA, GROUP_QB, GROUP_CAB)},
        ),
        ("DELETE FROM customer_user WHERE login IN :ids", {"ids": tuple(_CUSTOMERS)}),
        ("DELETE FROM customer_company WHERE customer_id IN :ids", {"ids": (FIRMA, ANDERE)}),
        ("DELETE FROM users WHERE id IN :ids", {"ids": users}),
    ]
    for sql, params in statements:
        await session.execute(text(sql).bindparams(bindparam("ids", expanding=True)), params)
    await session.flush()


def to_async_url(sync_url: str) -> str:
    return (
        sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
        .replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
        .replace("postgresql://", "postgresql+asyncpg://", 1)
    )


def ensure_tiqora_tables(sync_url: str) -> None:
    engine = create_engine(sync_url)
    try:
        with engine.begin() as conn:
            TiqoraBase.metadata.create_all(conn)
    finally:
        engine.dispose()


@asynccontextmanager
async def approvals_session(
    request: pytest.FixtureRequest, url_fixture: str, *, world: bool = True
) -> AsyncIterator[AsyncSession]:
    """A session whose whole transaction is rolled back at the end (the world with it).

    The session is bound to an outer connection transaction with savepoints, and the world is
    committed (released) into it: ``session.commit()`` and ``session.rollback()`` inside the
    code under test (routes roll back before deciding, F1(a)) only touch the savepoint, the
    seeded rows survive.
    """
    url = request.getfixturevalue(url_fixture)
    ensure_tiqora_tables(url)
    engine = create_async_engine(to_async_url(url), poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            outer = await conn.begin()
            factory = async_sessionmaker(
                conn,
                class_=AsyncSession,
                expire_on_commit=False,
                join_transaction_mode="create_savepoint",
            )
            try:
                async with factory() as session:
                    if world:
                        await seed_world(session)
                        await session.commit()
                    yield session
            finally:
                await outer.rollback()
    finally:
        await engine.dispose()


# --- registry ------------------------------------------------------------------------------------


class FakeSubjectType:
    """A subject type whose ``on_decided`` hook records its outcomes."""

    def __init__(self, key: str = "test.subject", *, allow_customer_approvers: bool = False):
        self.decided: list[ApprovalOutcome] = []
        self.raise_next: Exception | None = None
        self.item = ApprovalSubjectType(
            key=key,
            title_key=f"approvals.subject.{key}",
            allow_customer_approvers=allow_customer_approvers,
            on_decided=self.on_decided,
        )

    async def on_decided(self, session: AsyncSession, outcome: ApprovalOutcome) -> None:
        if self.raise_next is not None:
            error, self.raise_next = self.raise_next, None
            raise error
        self.decided.append(outcome)


def make_fake_subject_type(
    key: str = "test.subject", allow_customer_approvers: bool = False
) -> FakeSubjectType:
    return FakeSubjectType(key, allow_customer_approvers=allow_customer_approvers)


class _AnyPayload(BaseModel):
    model_config = ConfigDict(extra="allow")


async def _noop_job(ctx: Any, payload: Any) -> dict[str, Any] | None:
    return None


def registry_with(*items: ApprovalSubjectType | ApproverResolver | FakeSubjectType) -> Registry:
    """A fresh frozen registry: everything ``register_core`` adds plus the given test items."""
    registry = Registry()
    register_core(registry)
    # no-op handlers so enqueue_job accepts the approval jobs until the real ones are registered
    for kind in (JOB_HOOK, JOB_MAIL):
        if registry.get(JOB_HANDLERS, kind) is None:
            registry.add(
                JOB_HANDLERS,
                JobHandler(kind=kind, payload_model=_AnyPayload, run=_noop_job, title_key=kind),
                owner="test",
            )
    for item in items:
        if isinstance(item, FakeSubjectType):
            item = item.item
        point = (
            APPROVAL_SUBJECT_TYPES if isinstance(item, ApprovalSubjectType) else APPROVER_RESOLVERS
        )
        registry.add(point, item, owner="test")
    registry.freeze()
    return registry


@contextmanager
def installed_registry(
    *items: ApprovalSubjectType | ApproverResolver | FakeSubjectType,
) -> Iterator[Registry]:
    old = extensions_core._REGISTRY
    registry = registry_with(*items)
    set_registry_for_tests(registry)
    try:
        yield registry
    finally:
        set_registry_for_tests(old)


@pytest.fixture
def use_registry() -> Iterator[Any]:
    """``use_registry(*items)`` installs a registry with the items; the old one comes back."""
    old = extensions_core._REGISTRY
    try:

        def _install(*items: ApprovalSubjectType | ApproverResolver | FakeSubjectType) -> Registry:
            registry = registry_with(*items)
            set_registry_for_tests(registry)
            return registry

        yield _install
    finally:
        set_registry_for_tests(old)


# --- HTTP ----------------------------------------------------------------------------------------


async def as_client(
    session: AsyncSession, *, agent: int | None = None, customer: str | None = None
) -> AsyncClient:
    """An app on *session* acting as agent *agent* or portal customer *customer* (a login).

    Over an :func:`approvals_session` the routes' commit/rollback act on a savepoint. Use as
    ``async with await as_client(...) as client``.
    """
    from tiqora.api.app import create_app
    from tiqora.api.deps import get_current_user, get_db
    from tiqora.api.portal.deps import get_current_customer, require_portal_enabled
    from tiqora.config import Settings

    async def _db() -> Any:
        yield session

    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_db] = _db
    if agent is not None:
        user = AuthenticatedUser(
            id=agent,
            login=_AGENTS.get(agent, f"appr97-agent{agent}"),
            first_name="Appr",
            last_name="Agent",
            auth_method="session",
        )
        app.dependency_overrides[get_current_user] = lambda: user
    if customer is not None:
        email, company = _CUSTOMERS[customer]
        portal_user = AuthenticatedCustomer(
            id=1,
            login=customer,
            email=email,
            customer_id=company,
            first_name="Kunde",
            last_name=customer,
        )
        app.dependency_overrides[require_portal_enabled] = lambda: None
        app.dependency_overrides[get_current_customer] = lambda: portal_user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# --- mail ----------------------------------------------------------------------------------------


class CapturingMail:
    """Replaces ``build_outbound_sender`` with one shared :class:`CapturingMailSender`.

    Patches the module attribute: code under test must call
    ``mail_outbound.build_outbound_sender(...)`` through the module (a
    ``from ... import build_outbound_sender`` binding bypasses the patch).
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tiqora.domain import mail_outbound

        self.sender = CapturingMailSender()

        async def _build(session: Any, **kwargs: Any) -> CapturingMailSender:
            return self.sender

        monkeypatch.setattr(mail_outbound, "build_outbound_sender", _build)

    @property
    def sent(self) -> list[Any]:
        return self.sender.sent
