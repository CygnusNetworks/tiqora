"""State a follow-up mail puts its ticket into (Znuny PostMaster::FollowUp
parity, ``FollowUp.pm`` lines 173-191).

Znuny applies ``PostmasterFollowUpState`` ("open") to every ticket that is not
"new" and ``PostmasterFollowUpStateClosed`` to a closed one; a new ticket stays
new. Tiqora used to do the opposite for the non-closed case (new → open,
pending stayed pending), so a customer's answer to a "Wartend" ticket went
unnoticed until the reminder.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.channels.email.pipeline import process_message

from ._row_cleanup import cleanup_module
from .test_mail_log import _ensure_tables, _make_sysconfig, _raw_email, _to_async_url
from .test_postmaster_followup_history import _account, _history_types


@pytest.fixture(autouse=True, scope="module")
def _cleanup(mariadb_znuny_url: str) -> Iterator[None]:
    yield from cleanup_module(mariadb_znuny_url)


async def _state(session: AsyncSession, ticket_id: int) -> str:
    return str(
        (
            await session.execute(
                text(
                    "SELECT ts.name FROM ticket t JOIN ticket_state ts"
                    " ON ts.id = t.ticket_state_id WHERE t.id = :tid"
                ),
                {"tid": ticket_id},
            )
        ).scalar_one()
    )


async def _new_ticket_then_followup(
    mariadb_znuny_url: str, subject: str, *, state_before: str | None
) -> tuple[int, str, list[str]]:
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
                    subject=subject,
                    from_addr="customer@example.com",
                    to_addr="support@example.com",
                    body="First contact",
                ),
                account=account,
                user_id=1,
            )
        ticket_id = created.ticket_id
        assert ticket_id is not None
        async with factory() as session, session.begin():
            tn = str(
                (
                    await session.execute(
                        text("SELECT tn FROM ticket WHERE id = :tid"), {"tid": ticket_id}
                    )
                ).scalar_one()
            )
            if state_before is not None:
                await session.execute(
                    text(
                        "UPDATE ticket SET ticket_state_id ="
                        " (SELECT id FROM ticket_state WHERE name = :n) WHERE id = :tid"
                    ),
                    {"n": state_before, "tid": ticket_id},
                )
        async with factory() as session, session.begin():
            followup = await process_message(
                session,
                factory,
                sysconfig,
                raw=_raw_email(
                    subject=f"Re: [Ticket#{tn}] {subject}",
                    from_addr="customer@example.com",
                    to_addr="support@example.com",
                    body="An answer",
                ),
                account=account,
                user_id=1,
            )
        assert followup.outcome == "follow_up"
        async with factory() as session:
            return (
                ticket_id,
                await _state(session, ticket_id),
                await _history_types(session, ticket_id),
            )
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_followup_wakes_a_pending_ticket(mariadb_znuny_url: str) -> None:
    _, state, history = await _new_ticket_then_followup(
        mariadb_znuny_url, "Followup state pending", state_before="pending reminder"
    )
    assert state == "open"
    assert "StateUpdate" in history


@pytest.mark.db
async def test_followup_reopens_a_closed_ticket(mariadb_znuny_url: str) -> None:
    _, state, _history = await _new_ticket_then_followup(
        mariadb_znuny_url, "Followup state closed", state_before="closed successful"
    )
    assert state == "open"


@pytest.mark.db
async def test_followup_leaves_a_new_ticket_new(mariadb_znuny_url: str) -> None:
    _, state, history = await _new_ticket_then_followup(
        mariadb_znuny_url, "Followup state new", state_before=None
    )
    assert state == "new"
    assert "StateUpdate" not in history


@pytest.mark.db
async def test_followup_on_an_open_ticket_writes_no_state_update(mariadb_znuny_url: str) -> None:
    _, state, history = await _new_ticket_then_followup(
        mariadb_znuny_url, "Followup state open", state_before="open"
    )
    assert state == "open"
    assert "StateUpdate" not in history
