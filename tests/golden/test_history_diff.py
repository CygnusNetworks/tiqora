"""Golden-master: ticket_history row format diff between Znuny and Tiqora.

Performs an equivalent sequence of operations (create, state change, queue
move, priority, owner, note, close) on twin tickets — one driven through a
real Znuny Perl one-liner, one through Tiqora's ``ticket_write_service`` —
then dumps and diffs the resulting ``ticket_history`` rows, normalized (ids
and timestamps masked so only the history *shape* is compared).
"""

from __future__ import annotations

import re

import pytest
from _helpers import znuny_perl_eval

pytestmark = pytest.mark.golden

_ZNUNY_LIFECYCLE_PERL = r"""
use lib qw(/opt/otrs /opt/otrs/Kernel/cpan-lib /opt/otrs/Custom);
use Kernel::System::ObjectManager;
local $Kernel::OM = Kernel::System::ObjectManager->new(
    'Kernel::System::Log' => {LogPrefix => 'golden-master'},
);
my $TicketObject = $Kernel::OM->Get('Kernel::System::Ticket');

my $TicketID = $TicketObject->TicketCreate(
    Title        => 'Golden lifecycle',
    Queue        => 'Golden',
    Lock         => 'unlock',
    Priority     => '3 normal',
    State        => 'new',
    CustomerID   => 'golden-customer',
    CustomerUser => 'golden.customer',
    OwnerID      => 1,
    UserID       => 1,
);
$TicketObject->TicketStateSet(TicketID => $TicketID, State => 'open', UserID => 1);
$TicketObject->TicketQueueSet(TicketID => $TicketID, Queue => 'Raw', UserID => 1);
$TicketObject->TicketPrioritySet(TicketID => $TicketID, Priority => '4 high', UserID => 1);
$TicketObject->TicketOwnerSet(TicketID => $TicketID, NewUserID => 1, UserID => 1);
my $ArticleObject = $Kernel::OM->Get('Kernel::System::Ticket::Article');
my $ArticleBackendObject = $ArticleObject->BackendForChannel(ChannelName => 'Internal');
$ArticleBackendObject->ArticleCreate(
    TicketID             => $TicketID,
    SenderType           => 'agent',
    IsVisibleForCustomer => 0,
    From                 => 'golden@example.invalid',
    Subject              => 'Golden note',
    Body                 => 'Golden note body',
    ContentType          => 'text/plain; charset=utf8',
    HistoryType          => 'AddNote',
    # The AddNote history name is caller-supplied (HistoryComment) in Znuny;
    # Tiqora's add_article generates "%% {subject}". Align the caller data so
    # the diff only tests genuinely Znuny-controlled formats.
    HistoryComment       => '%% Golden note',
    UserID               => 1,
);
$TicketObject->TicketStateSet(TicketID => $TicketID, State => 'closed successful', UserID => 1);

print $TicketID;
"""


def _znuny_run_lifecycle() -> int:
    out = znuny_perl_eval(_ZNUNY_LIFECYCLE_PERL).strip()
    return int(out)


async def _id_by_name(conn_session, table: str, name_col: str, name: str) -> int:
    from sqlalchemy import text

    row = (
        await conn_session.execute(
            text(f"SELECT id FROM {table} WHERE {name_col} = :name LIMIT 1"),  # noqa: S608
            {"name": name},
        )
    ).first()
    assert row is not None, f"{table}.{name_col} = {name!r} not found (was the golden DB seeded?)"
    return int(row[0])


async def _tiqora_run_lifecycle(golden_session_factory) -> int:
    """Equivalent lifecycle through Tiqora's own domain write path.

    Uses tiqora.domain.ticket_write_service directly (the same code path the
    compat/API layers call) so the golden-master comparison exercises real
    production code, not a re-implementation.
    """
    from tiqora.domain.ticket_write_service import (
        ArticleIn,
        TicketIn,
        add_article,
        assign_owner,
        change_priority,
        change_state,
        create_ticket,
        move_queue,
    )
    from tiqora.znuny.sysconfig import SysConfig
    from tiqora.znuny.ticket_number import ticket_create_number  # noqa: F401 (parity import)

    async with golden_session_factory() as session:
        golden_queue_id = await _id_by_name(session, "queue", "name", "Golden")
        raw_queue_id = await _id_by_name(session, "queue", "name", "Raw")
        state_new_id = await _id_by_name(session, "ticket_state", "name", "new")
        state_open_id = await _id_by_name(session, "ticket_state", "name", "open")
        state_closed_id = await _id_by_name(session, "ticket_state", "name", "closed successful")
        prio_normal_id = await _id_by_name(session, "ticket_priority", "name", "3 normal")
        prio_high_id = await _id_by_name(session, "ticket_priority", "name", "4 high")

    async with golden_session_factory() as session:
        sysconfig = SysConfig(session)
        ticket_id = await create_ticket(
            session,
            golden_session_factory,
            sysconfig,
            params=TicketIn(
                title="Golden lifecycle",
                queue_id=golden_queue_id,
                state_id=state_new_id,
                priority_id=prio_normal_id,
                owner_id=1,
                customer_id="golden-customer",
                customer_user_id="golden.customer",
            ),
            user_id=1,
        )
        await session.commit()

    async with golden_session_factory() as session:
        sysconfig = SysConfig(session)
        await change_state(
            session, ticket_id=ticket_id, new_state_id=state_open_id, user_id=1, sysconfig=sysconfig
        )
        await session.commit()
    async with golden_session_factory() as session:
        sysconfig = SysConfig(session)
        await move_queue(
            session, ticket_id=ticket_id, new_queue_id=raw_queue_id, user_id=1, sysconfig=sysconfig
        )
        await session.commit()
    async with golden_session_factory() as session:
        sysconfig = SysConfig(session)
        await change_priority(
            session,
            ticket_id=ticket_id,
            new_priority_id=prio_high_id,
            user_id=1,
            sysconfig=sysconfig,
        )
        await session.commit()
    async with golden_session_factory() as session:
        sysconfig = SysConfig(session)
        await assign_owner(
            session, ticket_id=ticket_id, new_owner_id=1, user_id=1, sysconfig=sysconfig
        )
        await session.commit()
    async with golden_session_factory() as session:
        sysconfig = SysConfig(session)
        await add_article(
            session,
            ticket_id=ticket_id,
            article=ArticleIn(
                sender_type="agent",
                is_visible_for_customer=False,
                subject="Golden note",
                body="Golden note body",
                channel="note",
            ),
            user_id=1,
            sysconfig=sysconfig,
        )
        await session.commit()
    async with golden_session_factory() as session:
        sysconfig = SysConfig(session)
        await change_state(
            session,
            ticket_id=ticket_id,
            new_state_id=state_closed_id,
            user_id=1,
            sysconfig=sysconfig,
        )
        await session.commit()

    return ticket_id


_NUMBER_RE = re.compile(r"\d+")


def _dump_history(golden_conn, ticket_id: int) -> list[dict]:
    with golden_conn.cursor() as cur:
        cur.execute(
            """
            SELECT h.history_type_id, t.name AS history_type, h.name
            FROM ticket_history h
            JOIN ticket_history_type t ON t.id = h.history_type_id
            WHERE h.ticket_id = %s
            ORDER BY h.id ASC
            """,
            (ticket_id,),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]


def _normalize(rows: list[dict]) -> list[tuple[str, str]]:
    """Mask ticket-specific numeric substrings (TicketID, TN, queue ids, …)."""
    out = []
    for row in rows:
        masked_name = _NUMBER_RE.sub("#", row["name"])
        out.append((row["history_type"], masked_name))
    return out


@pytest.mark.asyncio
async def test_history_rows_match_znuny(golden_session_factory, golden_conn) -> None:
    znuny_ticket_id = _znuny_run_lifecycle()
    tiqora_ticket_id = await _tiqora_run_lifecycle(golden_session_factory)

    znuny_rows = _normalize(_dump_history(golden_conn, znuny_ticket_id))
    tiqora_rows = _normalize(_dump_history(golden_conn, tiqora_ticket_id))

    assert [t for t, _ in znuny_rows] == [t for t, _ in tiqora_rows], (
        f"history_type sequence differs:\nznuny:  {[t for t, _ in znuny_rows]}\n"
        f"tiqora: {[t for t, _ in tiqora_rows]}"
    )

    mismatches = [
        (i, z, t) for i, (z, t) in enumerate(zip(znuny_rows, tiqora_rows, strict=True)) if z != t
    ]
    assert not mismatches, (
        "ticket_history name format mismatches (index, znuny, tiqora):\n"
        + "\n".join(f"  [{i}] znuny={z!r} tiqora={t!r}" for i, z, t in mismatches)
    )
