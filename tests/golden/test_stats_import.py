"""Golden-master: Znuny statistics imported by the real peer map like the export files.

The fixture XML files of ``backend/tests/fixtures/znuny_stats`` are imported through the peer's
``Kernel::System::Stats->Import``; Tiqora then reads the resulting ``xml_storage`` rows
(``load_stats``) and maps them (``map_stat``). The targets, dimensions, measures and issues must
equal those of the same file mapped directly (``xml_file_to_hash``), i.e. what the pure tests
assert. Manual: ``GOLDEN=1 GOLDEN_PEER=znuny-6.5 just golden-up && just golden-seed``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from _helpers import znuny_copy_in, znuny_perl_eval
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.reporting.znuny_stats.mapper import map_stat
from tiqora.reporting.znuny_stats.model import stat_from_xml_hash
from tiqora.reporting.znuny_stats.reader import load_stat_lookups, load_stats
from tiqora.reporting.znuny_stats.xml_hash import xml_file_to_hash

pytestmark = pytest.mark.golden

FIX = Path(__file__).resolve().parents[2] / "backend" / "tests" / "fixtures" / "znuny_stats"
FILES = (
    "matrix_time_axis.xml",
    "accounted_time.xml",
    "solution_response.xml",
    "Stats.TestTicketList.en.xml",
)
_IMPORT = r"""
use lib qw(/opt/otrs /opt/otrs/Kernel/cpan-lib /opt/otrs/Custom);
use Kernel::System::ObjectManager;
local $Kernel::OM = Kernel::System::ObjectManager->new(
    'Kernel::System::Log' => {LogPrefix => 'golden'});
open my $Fh, '<', $ARGV[0] or die "open: $!";
local $/;
my $Content = <$Fh>;
close $Fh;
my $StatID = $Kernel::OM->Get('Kernel::System::Stats')->Import(UserID => 1, Content => $Content);
die "Import failed" unless $StatID;
print "$StatID\n";
"""
_DELETE = r"""
use lib qw(/opt/otrs /opt/otrs/Kernel/cpan-lib /opt/otrs/Custom);
use Kernel::System::ObjectManager;
local $Kernel::OM = Kernel::System::ObjectManager->new(
    'Kernel::System::Log' => {LogPrefix => 'golden'});
$Kernel::OM->Get('Kernel::System::Stats')->StatsDelete(StatID => $ARGV[0], UserID => 1);
"""


def _perl_with_arg(code: str, arg: str) -> str:
    # znuny_perl_eval has no argv; the single argument is spliced in as a quoted literal.
    return znuny_perl_eval(code.replace("$ARGV[0]", repr(arg)))


@pytest_asyncio.fixture()
async def imported_ids() -> AsyncIterator[dict[str, int]]:
    ids: dict[str, int] = {}
    try:
        for name in FILES:
            remote = f"/tmp/golden-stat-{name}"  # noqa: S108 -- inside the peer container
            znuny_copy_in(FIX / name, remote)
            ids[name] = int(_perl_with_arg(_IMPORT, remote).strip().splitlines()[-1])
        yield ids
    finally:
        for stat_id in ids.values():
            _perl_with_arg(_DELETE, str(stat_id))


@pytest.mark.asyncio
async def test_peer_import_maps_like_the_file(
    golden_session_factory: async_sessionmaker[AsyncSession], imported_ids: dict[str, int]
) -> None:
    async with golden_session_factory() as session:
        stats, errors, _truncated = await load_stats(session, list(imported_ids.values()))
        lookups = await load_stat_lookups(session, 1)
    assert errors == []
    by_id = {s.stat_id: s for s in stats}
    for name, stat_id in imported_ids.items():
        from_db = map_stat(by_id[stat_id], lookups, is_admin=True)
        from_file = map_stat(
            stat_from_xml_hash(
                xml_file_to_hash((FIX / name).read_bytes()), origin="file", stat_id=None
            ),
            lookups,
            is_admin=True,
        )
        assert from_db.target == from_file.target, name
        assert from_db.target != "none", name
        if from_db.definition is not None:
            assert from_file.definition is not None
            assert [d.key for d in from_db.definition.dimensions] == [
                d.key for d in from_file.definition.dimensions
            ], name
            assert [m.key for m in from_db.definition.measures] == [
                m.key for m in from_file.definition.measures
            ], name
