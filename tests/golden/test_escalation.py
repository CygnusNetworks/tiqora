"""Golden-master: escalation column comparison between Znuny and Tiqora.

The ``Golden`` queue (seeded by ``tests/golden/seed.sh``) has
first_response/update/solution times configured. This creates twin tickets
in that queue via both paths and compares the four
``ticket.escalation_*`` columns, then closes both and checks they zero out
(``tiqora.znuny.escalation`` docstring flags this as a divergence-prone area
— DST-adjacent working-time math — hence a dedicated golden test).
"""

from __future__ import annotations

import pytest
from _helpers import znuny_perl_eval

pytestmark = pytest.mark.golden

_ESCALATION_COLUMNS = (
    "escalation_time",
    "escalation_response_time",
    "escalation_update_time",
    "escalation_solution_time",
)

_ZNUNY_CREATE_IN_GOLDEN_QUEUE = r"""
use lib qw(/opt/otrs /opt/otrs/Kernel/cpan-lib /opt/otrs/Custom);
use Kernel::System::ObjectManager;
local $Kernel::OM = Kernel::System::ObjectManager->new(
    'Kernel::System::Log' => {LogPrefix => 'golden-master'},
);
my $TicketObject = $Kernel::OM->Get('Kernel::System::Ticket');
my $TicketID = $TicketObject->TicketCreate(
    Title        => 'Golden escalation',
    Queue        => 'Golden',
    Lock         => 'unlock',
    Priority     => '3 normal',
    State        => 'new',
    CustomerID   => 'golden-customer',
    CustomerUser => 'golden.customer',
    OwnerID      => 1,
    UserID       => 1,
);
print $TicketID;
"""

_ZNUNY_CLOSE = r"""
use lib qw(/opt/otrs /opt/otrs/Kernel/cpan-lib /opt/otrs/Custom);
use Kernel::System::ObjectManager;
local $Kernel::OM = Kernel::System::ObjectManager->new(
    'Kernel::System::Log' => {LogPrefix => 'golden-master'},
);
my $TicketObject = $Kernel::OM->Get('Kernel::System::Ticket');
$TicketObject->TicketStateSet(TicketID => %d, State => 'closed successful', UserID => 1);
print "ok";
"""


def _escalation_row(golden_conn, ticket_id: int) -> dict[str, int]:
    with golden_conn.cursor() as cur:
        cur.execute(
            f"SELECT {', '.join(_ESCALATION_COLUMNS)} FROM ticket WHERE id = %s",  # noqa: S608
            (ticket_id,),
        )
        row = cur.fetchone()
    assert row is not None, f"ticket {ticket_id} not found"
    return dict(zip(_ESCALATION_COLUMNS, row, strict=True))


@pytest.mark.asyncio
async def test_escalation_columns_match_and_zero_on_close(
    golden_session_factory, golden_conn
) -> None:
    from _helpers import GOLDEN_DIR  # noqa: F401 (kept for readability of intent)
    from sqlalchemy import text

    from tiqora.domain.ticket_write_service import TicketIn, change_state, create_ticket
    from tiqora.znuny.sysconfig import SysConfig

    znuny_ticket_id = int(znuny_perl_eval(_ZNUNY_CREATE_IN_GOLDEN_QUEUE).strip())

    async with golden_session_factory() as session:
        golden_queue_id = int(
            (await session.execute(text("SELECT id FROM queue WHERE name = 'Golden'"))).scalar_one()
        )
        state_new_id = int(
            (
                await session.execute(text("SELECT id FROM ticket_state WHERE name = 'new'"))
            ).scalar_one()
        )
        prio_id = int(
            (
                await session.execute(
                    text("SELECT id FROM ticket_priority WHERE name = '3 normal'")
                )
            ).scalar_one()
        )

    async with golden_session_factory() as session:
        sysconfig = SysConfig(session)
        tiqora_ticket_id = await create_ticket(
            session,
            golden_session_factory,
            sysconfig,
            params=TicketIn(
                title="Golden escalation",
                queue_id=golden_queue_id,
                state_id=state_new_id,
                priority_id=prio_id,
                owner_id=1,
                customer_id="golden-customer",
                customer_user_id="golden.customer",
            ),
            user_id=1,
        )
        await session.commit()

    znuny_esc = _escalation_row(golden_conn, znuny_ticket_id)
    tiqora_esc = _escalation_row(golden_conn, tiqora_ticket_id)

    # Compare destination-time *offsets* from creation (both computed "now"),
    # not absolute epoch (tickets are created a few seconds apart). Znuny sets
    # escalation_response_time = create_time_epoch + first_response_time*60
    # when unanswered; same for update/solution. escalation_time is the min
    # of the set ones. Allow small skew for wall-clock drift between the two
    # TicketCreate calls (a few seconds, not the ±1h DST case called out in
    # the docstring).
    for col in _ESCALATION_COLUMNS:
        z, t = znuny_esc[col], tiqora_esc[col]
        if z == 0 and t == 0:
            continue
        assert z != 0 and t != 0, f"{col}: one side is zero (znuny={z}, tiqora={t})"
        assert abs(z - t) <= 30, f"{col}: znuny={z} tiqora={t} differ by more than 30s"

    # Close both; all four columns must zero out.
    znuny_perl_eval(_ZNUNY_CLOSE % znuny_ticket_id)
    async with golden_session_factory() as session:
        sysconfig = SysConfig(session)
        state_closed_id = int(
            (
                await session.execute(
                    text("SELECT id FROM ticket_state WHERE name = 'closed successful'")
                )
            ).scalar_one()
        )
        await change_state(
            session,
            ticket_id=tiqora_ticket_id,
            new_state_id=state_closed_id,
            user_id=1,
            sysconfig=sysconfig,
        )
        await session.commit()

    znuny_esc_closed = _escalation_row(golden_conn, znuny_ticket_id)
    tiqora_esc_closed = _escalation_row(golden_conn, tiqora_ticket_id)
    for col in _ESCALATION_COLUMNS:
        assert znuny_esc_closed[col] == 0, (
            f"znuny {col} not zeroed on close: {znuny_esc_closed[col]}"
        )
        assert tiqora_esc_closed[col] == 0, (
            f"tiqora {col} not zeroed on close: {tiqora_esc_closed[col]}"
        )
