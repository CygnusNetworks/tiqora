"""Postmaster follow-up history type (Znuny PostMaster::FollowUp parity).

Znuny writes ``FollowUp`` history for a follow-up mail and ``EmailCustomer``
for a new ticket's first article (``PostMaster/FollowUp.pm`` line 562 vs
``PostMaster/NewTicket.pm`` line 575). The history type is what tells the two
apart in the ticket history — and what decides whether the legacy
``NotificationFollowUp`` event fires for the notification engine.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.channels.email.pipeline import process_message
from tiqora.db.legacy.mail_account import MailAccount

from ._row_cleanup import cleanup_module
from .test_mail_log import (
    NOW,
    _ensure_tables,
    _make_sysconfig,
    _raw_email,
    _to_async_url,
)


@pytest.fixture(autouse=True, scope="module")
def _cleanup(mariadb_znuny_url: str) -> Iterator[None]:
    """Delete the ticket, articles and log rows the pipeline commits."""
    yield from cleanup_module(mariadb_znuny_url)


def _account() -> MailAccount:
    return MailAccount(
        id=1,
        login="postmaster@example.com",
        pw="x",
        host="localhost",
        account_type="IMAP",
        queue_id=1,
        trusted=0,
        valid_id=1,
        create_time=NOW,
        create_by=1,
        change_time=NOW,
        change_by=1,
    )


async def _history_types(session: AsyncSession, ticket_id: int) -> list[str]:
    rows = (
        await session.execute(
            text(
                "SELECT ht.name FROM ticket_history h"
                " JOIN ticket_history_type ht ON ht.id = h.history_type_id"
                " WHERE h.ticket_id = :tid ORDER BY h.id"
            ),
            {"tid": ticket_id},
        )
    ).fetchall()
    return [str(r[0]) for r in rows]


async def _outbox_types(session: AsyncSession, ticket_id: int) -> list[str]:
    rows = (
        await session.execute(
            text("SELECT event_type FROM tiqora_event_outbox WHERE ticket_id = :tid ORDER BY id"),
            {"tid": ticket_id},
        )
    ).fetchall()
    return [str(r[0]) for r in rows]


@pytest.mark.db
async def test_follow_up_writes_followup_history_and_event(mariadb_znuny_url: str) -> None:
    _ensure_tables(mariadb_znuny_url)
    engine = create_async_engine(_to_async_url(mariadb_znuny_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    sysconfig = _make_sysconfig()
    account = _account()
    try:
        async with factory() as session, session.begin():
            created = await process_message(
                session,
                factory,
                sysconfig,
                raw=_raw_email(
                    subject="Followup history parity",
                    from_addr="customer@example.com",
                    to_addr="support@example.com",
                    body="First contact",
                ),
                account=account,
                user_id=1,
            )
        assert created.outcome == "new_ticket"
        ticket_id = created.ticket_id
        assert ticket_id is not None
        async with factory() as session:
            row = (
                await session.execute(
                    text("SELECT tn FROM ticket WHERE id = :tid"), {"tid": ticket_id}
                )
            ).first()
        assert row is not None
        tn = str(row[0])

        async with factory() as session, session.begin():
            followup = await process_message(
                session,
                factory,
                sysconfig,
                raw=_raw_email(
                    subject=f"Re: [Ticket#{tn}] Followup history parity",
                    from_addr="customer@example.com",
                    to_addr="support@example.com",
                    body="Still broken",
                ),
                account=account,
                user_id=1,
            )
        assert followup.outcome == "follow_up"
        assert followup.ticket_id == ticket_id

        async with factory() as session:
            history = await _history_types(session, ticket_id)
            events = await _outbox_types(session, ticket_id)

        # New ticket keeps EmailCustomer; the follow-up is FollowUp, not a
        # second EmailCustomer row.
        assert history.count("EmailCustomer") == 1
        assert history.count("FollowUp") == 1

        # Which in turn is what makes the two legacy notification events
        # distinguishable for rules bound to them.
        assert events.count("NotificationNewTicket") == 1
        assert events.count("NotificationFollowUp") == 1
    finally:
        await engine.dispose()
