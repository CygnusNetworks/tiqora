"""Deterministic fake-data seeding for local/dev databases (``tiqora dev seed``).

Customer companies/users are inserted directly (they carry no Tiqora-side
invariants beyond FK validity). Tickets and articles are written through
:class:`~tiqora.domain.ticket_write_service.TicketWriteService` so history
rows, escalation columns, cache invalidation, and outbox events all get
created exactly as they would via the API — this is the whole reason to
route seeding through the write service instead of raw INSERTs.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.db.legacy.customer import CustomerCompany, CustomerUser
from tiqora.db.legacy.queue import Queue
from tiqora.db.legacy.ticket import TicketPriority, TicketState
from tiqora.domain.ticket_write_service import ArticleIn, TicketIn, TicketWriteService
from tiqora.permissions.engine import PermissionEngine
from tiqora.znuny.sysconfig import SysConfig


class SeedError(Exception):
    """Raised when the target database is not suitable for seeding."""


@dataclass
class SeedResult:
    customers_created: int
    customer_users_created: int
    tickets_created: int
    articles_created: int


def _require_faker() -> type:
    try:
        from faker import Faker
    except ImportError as exc:  # pragma: no cover - exercised only w/o faker installed
        raise SeedError(
            "The 'faker' package is required for `tiqora dev seed` but is not "
            "installed. It ships in the backend 'dev' dependency group — run "
            "`uv sync --all-extras` (or `uv sync --group dev`) from backend/."
        ) from exc
    return Faker


async def seed_database(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    customers: int,
    tickets: int,
    seed: int | None = None,
    agent_user_id: int = 1,
) -> SeedResult:
    """Seed *customers* customer companies (1-3 users each) and *tickets* tickets.

    Deterministic given ``seed``: Faker's instance RNG and the stdlib
    ``random`` module (used to pick queues/states/priorities/articles) are
    both seeded, so the same ``--seed`` reproduces the same generated
    content. Primary-key-facing identifiers (customer_id, login) still carry
    a random per-run nonce so repeated invocations against the same database
    never collide on uniqueness constraints.

    Tickets are only created in queues where *agent_user_id* holds the
    ``create`` permission (via :class:`PermissionEngine`) — this mirrors the
    real write-service permission gate rather than bypassing it.
    """
    Faker = _require_faker()
    fake = Faker()
    if seed is not None:
        fake.seed_instance(seed)
        random.seed(seed)

    async with session_factory() as session:
        perms = PermissionEngine(session)
        allowed_groups = await perms.groups_for_permission(agent_user_id, "create")
        if not allowed_groups:
            raise SeedError(
                f"Agent user {agent_user_id} has no 'create' permission on any queue "
                "group — cannot seed tickets. Pick an agent with queue access via "
                "--agent-user-id, or grant permissions first."
            )
        queue_ids = list(
            (
                await session.execute(
                    select(Queue.id).where(Queue.group_id.in_(allowed_groups), Queue.valid_id == 1)
                )
            )
            .scalars()
            .all()
        )
        if not queue_ids:
            raise SeedError(
                "No valid queues found for the acting agent's permitted groups — "
                "seeding assumes an initialized Znuny schema with at least one queue."
            )
        state_ids = list((await session.execute(select(TicketState.id))).scalars().all())
        priority_ids = list((await session.execute(select(TicketPriority.id))).scalars().all())
        if not state_ids or not priority_ids:
            raise SeedError(
                "No ticket states/priorities found — seeding assumes an initialized Znuny schema."
            )

    run_ns = uuid.uuid4().hex[:8]
    now = datetime.now(UTC).replace(tzinfo=None)

    customer_ids: list[str] = []
    customer_logins_by_company: dict[str, list[str]] = {}
    customer_users_created = 0

    async with session_factory() as session, session.begin():
        for i in range(customers):
            customer_id = f"SEED-{run_ns}-{i:04d}"
            session.add(
                CustomerCompany(
                    customer_id=customer_id,
                    name=fake.company(),
                    street=fake.street_address(),
                    zip=fake.postcode(),
                    city=fake.city(),
                    country=fake.country(),
                    valid_id=1,
                    create_time=now,
                    create_by=agent_user_id,
                    change_time=now,
                    change_by=agent_user_id,
                )
            )
            customer_ids.append(customer_id)
            logins: list[str] = []
            for j in range(random.randint(1, 3)):
                login = f"{fake.user_name()}.{run_ns}{i:04d}{j}"
                session.add(
                    CustomerUser(
                        login=login,
                        email=fake.email(),
                        customer_id=customer_id,
                        first_name=fake.first_name(),
                        last_name=fake.last_name(),
                        phone=fake.phone_number(),
                        valid_id=1,
                        create_time=now,
                        create_by=agent_user_id,
                        change_time=now,
                        change_by=agent_user_id,
                    )
                )
                logins.append(login)
                customer_users_created += 1
            customer_logins_by_company[customer_id] = logins

    tickets_created = 0
    articles_created = 0
    for _ in range(tickets):
        queue_id = random.choice(queue_ids)
        state_id = random.choice(state_ids)
        priority_id = random.choice(priority_ids)
        ticket_customer_id: str | None = None
        ticket_customer_login: str | None = None
        if customer_ids:
            ticket_customer_id = random.choice(customer_ids)
            logins = customer_logins_by_company.get(ticket_customer_id) or []
            ticket_customer_login = random.choice(logins) if logins else None

        n_articles = random.randint(1, 4)
        first_article = ArticleIn(
            sender_type="agent",
            is_visible_for_customer=True,
            subject=fake.sentence(nb_words=5).rstrip("."),
            body=fake.paragraph(nb_sentences=3),
            channel="note",
        )

        async with session_factory() as session:
            sysconfig = SysConfig(session)
            svc = TicketWriteService(session, session_factory, sysconfig)
            async with session.begin():
                ticket_id = await svc.create_ticket(
                    agent_user_id,
                    TicketIn(
                        title=fake.sentence(nb_words=6).rstrip("."),
                        queue_id=queue_id,
                        state_id=state_id,
                        priority_id=priority_id,
                        owner_id=agent_user_id,
                        customer_id=ticket_customer_id,
                        customer_user_id=ticket_customer_login,
                        article=first_article,
                    ),
                )
            tickets_created += 1
            articles_created += 1

            for _ in range(n_articles - 1):
                async with session.begin():
                    await svc.add_article(
                        agent_user_id,
                        ticket_id,
                        ArticleIn(
                            sender_type="agent",
                            is_visible_for_customer=True,
                            subject=fake.sentence(nb_words=5).rstrip("."),
                            body=fake.paragraph(nb_sentences=3),
                            channel="note",
                        ),
                    )
                articles_created += 1

    return SeedResult(
        customers_created=len(customer_ids),
        customer_users_created=customer_users_created,
        tickets_created=tickets_created,
        articles_created=articles_created,
    )
