"""Golden-master: DateChecksum ticket number generator vs real Znuny.

Switches ``Ticket::NumberGenerator`` to DateChecksum on the Znuny side
(SysConfig lives in the same DB Tiqora reads), generates a TN with a real
Znuny TicketCreate, and verifies Tiqora's ``format_date_checksum`` reproduces
the identical checksum digit for the same (date, SystemID, counter) triple —
i.e. Tiqora's port of the Bahn-style checksum algorithm in
``tiqora.znuny.ticket_number`` matches Znuny's ``Number/DateChecksum.pm``
bit for bit.
"""

from __future__ import annotations

import re

import pytest
from _helpers import znuny_console, znuny_perl_eval

from tiqora.znuny.ticket_number import format_date_checksum

pytestmark = pytest.mark.golden

_ZNUNY_CREATE = r"""
use lib qw(/opt/otrs /opt/otrs/Kernel/cpan-lib /opt/otrs/Custom);
use Kernel::System::ObjectManager;
local $Kernel::OM = Kernel::System::ObjectManager->new(
    'Kernel::System::Log' => {LogPrefix => 'golden-master'},
);
my $TicketObject = $Kernel::OM->Get('Kernel::System::Ticket');
my $TicketID = $TicketObject->TicketCreate(
    Title        => 'Golden checksum',
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

# yyyymmdd (8) + SystemID (variable, default "10") + counter (5) + checksum (1)
_TN_RE = re.compile(r"^(\d{8})(\d+?)(\d{5})(\d)$")


@pytest.fixture(scope="module", autouse=True)
def _switch_to_date_checksum():
    znuny_console(
        "Admin::Config::Update",
        "--setting-name=Ticket::NumberGenerator",
        "--value=Kernel::System::Ticket::Number::DateChecksum",
    )
    yield
    znuny_console(
        "Admin::Config::Update",
        "--setting-name=Ticket::NumberGenerator",
        "--value=Kernel::System::Ticket::Number::AutoIncrement",
    )


def test_date_checksum_matches_znuny() -> None:
    tn = znuny_perl_eval(_ZNUNY_CREATE).strip()
    m = _TN_RE.match(tn)
    assert m, f"TN {tn!r} does not match yyyymmdd+SystemID+counter(5)+checksum(1) shape"
    date_part, system_id, counter_part, znuny_checksum = m.groups()
    year, month, day = int(date_part[:4]), int(date_part[4:6]), int(date_part[6:8])
    counter = int(counter_part)

    tiqora_tn = format_date_checksum(counter, system_id, year, month, day)
    assert tiqora_tn == tn, (
        f"Tiqora format_date_checksum({counter}, {system_id!r}, {year}, {month}, {day}) "
        f"= {tiqora_tn!r} but Znuny issued {tn!r}"
    )
    assert tiqora_tn[-1] == znuny_checksum
