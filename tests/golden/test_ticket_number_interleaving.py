"""Golden-master: ticket number interleaving between Znuny and Tiqora.

Creates tickets alternately via a real Znuny console-driven TicketCreate and
via Tiqora's ``ticket_number.ticket_create_number`` against the SAME shared
``ticket_number_counter`` table, and asserts every produced TN is unique and
correctly formatted.
"""

from __future__ import annotations

import pytest
from _helpers import znuny_perl_eval

from tiqora.znuny.sysconfig import SysConfig
from tiqora.znuny.ticket_number import ticket_create_number

pytestmark = pytest.mark.golden

_ZNUNY_CREATE_TICKET_PERL = """
use lib qw(/opt/otrs /opt/otrs/Kernel/cpan-lib /opt/otrs/Custom);
use Kernel::System::ObjectManager;
local $Kernel::OM = Kernel::System::ObjectManager->new(
    'Kernel::System::Log' => {LogPrefix => 'golden-master'},
);
my $TicketObject = $Kernel::OM->Get('Kernel::System::Ticket');
my $TicketID = $TicketObject->TicketCreate(
    Title        => 'Golden interleave',
    Queue        => 'Golden',
    Lock         => 'unlock',
    Priority     => '3 normal',
    State        => 'new',
    CustomerID   => 'golden-customer',
    CustomerUser => 'golden.customer',
    OwnerID      => 1,
    UserID       => 1,
);
my %Ticket = $TicketObject->TicketGet(TicketID => $TicketID);
print $Ticket{TicketNumber};
"""


def _znuny_create_ticket_tn() -> str:
    return znuny_perl_eval(_ZNUNY_CREATE_TICKET_PERL).strip()


@pytest.mark.asyncio
async def test_ticket_numbers_unique_interleaved(golden_session_factory, golden_conn) -> None:
    async with golden_session_factory() as cfg_session:
        sysconfig = SysConfig(cfg_session)
        generator = await sysconfig.ticket_number_generator()

    tns: list[str] = []
    sides: list[str] = []
    for i in range(6):
        if i % 2 == 0:
            # Tiqora side: allocate a TN from the shared counter (the ticket
            # row itself is exercised in test_history_diff.py — here only the
            # counter interleaving is under test).
            async with golden_session_factory() as cfg_session:
                sysconfig = SysConfig(cfg_session)
                tns.append(await ticket_create_number(golden_session_factory, sysconfig))
            sides.append("tiqora")
        else:
            tns.append(_znuny_create_ticket_tn())
            sides.append("znuny")

    assert len(tns) == len(set(tns)), f"duplicate ticket numbers produced: {tns}"

    async with golden_session_factory() as cfg_session:
        system_id = await SysConfig(cfg_session).system_id()
    for tn in tns:
        assert system_id in tn, (
            f"TN {tn!r} does not look like a {generator} number for SystemID {system_id!r}"
        )

    # Znuny-created TNs must resolve to exactly one ticket row; Tiqora-side
    # allocations were counter-only (no ticket insert), so those TNs must NOT
    # exist yet — proving Znuny's collision-retry cannot re-issue them either
    # way (the counter is shared, not the ticket table).
    with golden_conn.cursor() as cur:
        for tn, side in zip(tns, sides, strict=True):
            cur.execute("SELECT COUNT(*) FROM ticket WHERE tn = %s", (tn,))
            (count,) = cur.fetchone()
            expected = 1 if side == "znuny" else 0
            assert count == expected, f"TN {tn!r} ({side}): expected {expected} rows, got {count}"
