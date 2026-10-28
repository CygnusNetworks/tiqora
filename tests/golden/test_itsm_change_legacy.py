# ruff: noqa: E501 - the embedded Perl keeps its natural line length
"""Golden: Znuny change management read-only parity (wave-5 itsm-change, Task 28).

Runs on the ITSM peers ``znuny-6.5-itsm`` (ITSMChangeManagement 6.5.2) and ``znuny-7.3-itsm``
(7.3.4) against the three ``chg94`` changes of ``itsm_seed.pl`` (work orders of every type, CAB
agent + customer, change/work-order dynamic fields, change->ticket and work order->CI/service/
ticket links, history written by the package's event modules):

* the legacy reader (``ZnunyLiveSource`` / ``reader.read_changes`` / ``reader.search``) equals
  ``ChangeGet`` / ``WorkOrderGet`` / ``ChangeCABGet`` / ``ChangeHistoryGet`` / ``LinkList`` of the
  peer, including the derived planned/actual times (ruling S2), the catalog names, the logins and
  the history values resolved like ``AgentITSMChangeHistory``;
* the Mode A API (``/api/v1/itsm/legacy-changes``) writes nothing: ordered ``SELECT *`` of the 20
  change tables and ``link_relation`` hashed before/after (ruling F33);
* the archive (``archive_changes``) stores every change so that ``ArchiveSource.get`` equals
  ``ZnunyLiveSource.get`` (no erasures: the tombstone table must be empty);
* the change setup rows (``ITSMChangeType`` / ``ITSMChangePhase``, ticket types ``Change`` /
  ``Change::Task``) load in Znuny's ``AdminDynamicFieldDropdown`` and its dynamic field backend;
* a change ticket and its task ticket created by Tiqora show in ``TicketGet`` with type and
  dynamic fields and as ``ParentChild`` from both ends.

    GOLDEN_PEER=znuny-6.5-itsm just golden-up && GOLDEN_PEER=znuny-6.5-itsm just golden-seed
    cd backend && GOLDEN=1 GOLDEN_PEER=znuny-6.5-itsm uv run python -m pytest -q -m golden \\
        ../tests/golden/test_itsm_change_legacy.py

Normalisations (representation only, never values): Znuny's API answers ``""`` where the reader
has ``None`` (unset times, empty texts, NULL history content), numbers as text (``"4.00"``), and
``*Plain`` texts with the line's surrounding newlines (ruling S3: both sides stripped). Znuny's
history screen shows ``name (ID=x)`` and joins CAB lists with ``,``; the reader keeps the raw
value plus the name and joins with ``, ``. ``LinkList`` reports every end of an unpointed link type
(``Normal``) as ``Source``, so the stored direction is compared for pointed types only.
``*NotificationSent`` content holds recipient addresses (ruling S5): both sides are compared as
SHA-256 so no address reaches the test output.

The module leaves the peer as it found it: every ``tiqora_*`` table it changed is restored from a
snapshot, the Znuny tickets it created are removed with ``TicketDelete``, the two dynamic fields
and ticket types the change setup created (when they were not there before) are deleted again.
Tiqora's writes reach Znuny's caches through the TiqoraSync add-on (copied in, ``Run()`` after
each Tiqora write, as the daemon cron task does in production).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import AsyncIterator, Generator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

import pymysql
import pytest
import yaml
from _helpers import znuny_console, znuny_copy_in, znuny_perl_eval
from _itsm_import_support import run_importer
from httpx import ASGITransport, AsyncClient
from peer_env import resolve as resolve_peer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests._itsm_legacy_helpers import change_checksums

from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db
from tiqora.config import Settings
from tiqora.domain.auth import AuthenticatedUser
from tiqora.itsm.change.legacy import mapping, reader
from tiqora.itsm.change.legacy.archive import ArchiveReport, archive_changes
from tiqora.itsm.change.legacy.model import LegacyChange, LegacyLink
from tiqora.itsm.change.legacy.sources import ArchiveSource, ZnunyLiveSource
from tiqora.itsm.seed_definitions import TIQORA_CHANGE_DYNAMIC_FIELDS
from tiqora.itsm.settings import ItsmSettings
from tiqora.itsm.znuny_adapter.detect import clear_detect_cache

pytestmark = [pytest.mark.golden, pytest.mark.itsm_peer]

GOLDEN_DIR = Path(__file__).resolve().parent
SEED_SCRIPT = GOLDEN_DIR / "itsm_seed.pl"
SEED_REMOTE = "/tmp/itsm_seed.pl"  # noqa: S108 - path inside the throw-away peer container
TIQORASYNC_MODULE = Path(
    os.environ.get("TIQORASYNC_MODULE")
    or GOLDEN_DIR.parents[1] / "packages/znuny-addon/TiqoraSync/Kernel/System/TiqoraSync.pm"
)
TIQORASYNC_PEER_PATH = "/opt/otrs/Kernel/System/TiqoraSync.pm"

SessionFactory = async_sessionmaker[AsyncSession]
SETTINGS = Settings(environment="test")
ROOT = 1  # root@localhost (admin)
SETTING_MODE = "itsm.mode"
#: titles of the tickets this module creates (leftovers of an aborted run are removed first)
TICKET_PREFIX = "chg94 golden"
#: rows the change setup creates (``itsm.setup`` group ``change``)
CHANGE_TYPES = ("Change", "Change::Task")
CHANGE_FIELDS = tuple(f.name for f in TIQORA_CHANGE_DYNAMIC_FIELDS)

#: base peer -> (schema profile, ITSMChangeManagement version installed by the toolkit)
EXPECTED_PEERS: dict[str, tuple[str, str]] = {
    "znuny-6.5": ("itsm-6", "6.5.2"),
    "znuny-7.3": ("itsm-7", "7.3.4"),
}


@dataclass(frozen=True)
class Peer:
    id: str
    base: str
    profile: str
    change_version: str


@pytest.fixture(scope="module")
def peer(golden_peer_id: str) -> Peer:
    base = resolve_peer(golden_peer_id)["GOLDEN_BASE_PEER"]
    profile, version = EXPECTED_PEERS[base]
    return Peer(golden_peer_id, base, profile, version)


# --- Perl side -------------------------------------------------------------------------------

_PRELUDE = r"""
use strict;
use warnings;
use lib qw(/opt/otrs /opt/otrs/Kernel/cpan-lib /opt/otrs/Custom);
use Digest::SHA ();
use Encode ();
use Kernel::System::ObjectManager;
local $Kernel::OM = Kernel::System::ObjectManager->new(
    'Kernel::System::Log' => { LogPrefix => 'golden-itsm-change-legacy' },
);
my $JSON         = $Kernel::OM->Get('Kernel::System::JSON');
my $LinkObject   = $Kernel::OM->Get('Kernel::System::LinkObject');
my $TicketObject = $Kernel::OM->Get('Kernel::System::Ticket');
my $DBObject     = $Kernel::OM->Get('Kernel::System::DB');
my $UserObject   = $Kernel::OM->Get('Kernel::System::User');

sub Emit { print "GOLDEN_JSON_BEGIN\n" . $JSON->Encode( Data => $_[0] ) . "\nGOLDEN_JSON_END\n"; }

sub Rows {
    my (%P) = @_;
    $DBObject->Prepare( SQL => $P{SQL}, Bind => $P{Bind} || [] ) or die "SQL failed: $P{SQL}";
    my @Rows;
    while ( my @Row = $DBObject->FetchrowArray() ) {
        push @Rows, [ map { defined $_ ? "$_" : undef } @Row ];
    }
    return \@Rows;
}

sub Login {
    my ($ID) = @_;
    return undef if !$ID;
    return scalar $UserObject->UserLookup( UserID => $ID, Silent => 1 );
}

# LinkList of one object as [ other object, other key, type, LinkList side ]
sub Links {
    my ( $Object, $Key ) = @_;
    my $List = $LinkObject->LinkList( Object => $Object, Key => $Key, State => 'Valid', UserID => 1 ) || {};
    my @Out;
    for my $Other ( sort keys %{$List} ) {
        for my $Type ( sort keys %{ $List->{$Other} } ) {
            for my $Side ( sort keys %{ $List->{$Other}->{$Type} } ) {
                push @Out, [ $Other, "$_", $Type, $Side ] for sort keys %{ $List->{$Other}->{$Type}->{$Side} };
            }
        }
    }
    return \@Out;
}

sub DynamicFields {
    my ($Data) = @_;
    my %Out;
    for my $Key ( sort grep {m{\ADynamicField_}} keys %{$Data} ) {
        my $Value = $Data->{$Key};
        next if !defined $Value || ( !ref $Value && $Value eq '' );
        ( my $Name = $Key ) =~ s{\ADynamicField_}{};
        $Out{$Name} = ref $Value eq 'ARRAY' ? [ map {"$_"} @{$Value} ] : ["$Value"];
    }
    return \%Out;
}
"""

_ZNUNY_CHANGES = r"""
my $ChangeObject  = $Kernel::OM->Get('Kernel::System::ITSMChange');
my $WOObject      = $Kernel::OM->Get('Kernel::System::ITSMChange::ITSMWorkOrder');
my $HistoryObject = $Kernel::OM->Get('Kernel::System::ITSMChange::History');

# a history value as AgentITSMChangeHistory resolves it (the name, without its " (ID=x)")
sub Resolved {
    my ( $Field, $Value ) = @_;
    return $Value if !defined $Field || !defined $Value || $Value eq '';
    return $ChangeObject->ChangeCIPLookup( ID => $Value, Type => $1 ) if $Field =~ m{\A(Category|Impact|Priority)ID\z};
    return $ChangeObject->ChangeStateLookup( ChangeStateID => $Value ) if $Field eq 'ChangeStateID';
    return $WOObject->WorkOrderStateLookup( WorkOrderStateID => $Value ) if $Field eq 'WorkOrderStateID';
    return $WOObject->WorkOrderTypeLookup( WorkOrderTypeID => $Value ) if $Field eq 'WorkOrderTypeID';
    return Login($Value) if $Field =~ m{\A(?:WorkOrderAgent|ChangeBuilder|ChangeManager)ID\z};
    return join ',', map { Login($_) } split m/%%/, $Value if $Field eq 'CABAgents';
    if ( $Field eq 'CABCustomers' ) { ( my $V = $Value ) =~ s{%%}{,}g; return $V; }
    return $Value;
}

# recipients of *NotificationSent rows are personal data (ruling S5): only their hash leaves Perl
sub Hidden {
    my ( $Type, $Value ) = @_;
    return $Value if !defined $Value || $Value eq '' || $Type !~ m{NotificationSent\z};
    return 'sha256:' . Digest::SHA::sha256_hex( Encode::encode_utf8($Value) );
}

my %Pointed;
my %Types = $LinkObject->TypeList( UserID => 1 );
$Pointed{$_} = ( $Types{$_}->{SourceName} ne $Types{$_}->{TargetName} ) ? 1 : 0 for keys %Types;

my $IDs = $ChangeObject->ChangeSearch( UserID => 1, Limit => 100000 ) || [];
my %Out;
for my $ChangeID ( @{$IDs} ) {
    my $Change = $ChangeObject->ChangeGet( ChangeID => $ChangeID, UserID => 1 ) or die "ChangeGet $ChangeID failed";
    my $CAB = $ChangeObject->ChangeCABGet( ChangeID => $ChangeID, UserID => 1 ) || {};
    my ( @WorkOrders, %Number );
    for my $WOID ( @{ $Change->{WorkOrderIDs} || [] } ) {
        my $WO = $WOObject->WorkOrderGet( WorkOrderID => $WOID, UserID => 1 ) or die "WorkOrderGet $WOID failed";
        $Number{$WOID} = $WO->{WorkOrderNumber} + 0;
        push @WorkOrders, {
            number => $WO->{WorkOrderNumber} + 0, title => $WO->{WorkOrderTitle},
            type => $WO->{WorkOrderType}, state => $WO->{WorkOrderState},
            agent => Login( $WO->{WorkOrderAgentID} ),
            planned_start => $WO->{PlannedStartTime}, planned_end => $WO->{PlannedEndTime},
            actual_start => $WO->{ActualStartTime}, actual_end => $WO->{ActualEndTime},
            planned_effort => $WO->{PlannedEffort}, accounted_time => $WO->{AccountedTime},
            instruction => $WO->{InstructionPlain}, report => $WO->{ReportPlain},
            dynamic_fields => DynamicFields($WO), links => Links( 'ITSMWorkOrder', $WOID ),
        };
    }
    my @History;
    for my $H ( @{ $HistoryObject->ChangeHistoryGet( ChangeID => $ChangeID, UserID => 1 ) || [] } ) {
        push @History, {
            type => $H->{HistoryType}, field => $H->{Fieldname},
            old => Hidden( $H->{HistoryType}, $H->{ContentOld} ), new => Hidden( $H->{HistoryType}, $H->{ContentNew} ),
            old_name => Hidden( $H->{HistoryType}, Resolved( $H->{Fieldname}, $H->{ContentOld} ) ),
            new_name => Hidden( $H->{HistoryType}, Resolved( $H->{Fieldname}, $H->{ContentNew} ) ),
            created => $H->{CreateTime}, login => $H->{UserLogin},
            workorder => ( $H->{WorkOrderID} ? ( $Number{ $H->{WorkOrderID} } // 0 ) : 0 ),
        };
    }
    $Out{$ChangeID} = {
        number => $Change->{ChangeNumber}, title => $Change->{ChangeTitle},
        state => $Change->{ChangeState}, category => $Change->{Category},
        impact => $Change->{Impact}, priority => $Change->{Priority},
        manager => Login( $Change->{ChangeManagerID} ), builder => Login( $Change->{ChangeBuilderID} ),
        requested_time => $Change->{RequestedTime},
        planned_start => $Change->{PlannedStartTime}, planned_end => $Change->{PlannedEndTime},
        actual_start => $Change->{ActualStartTime}, actual_end => $Change->{ActualEndTime},
        created => $Change->{CreateTime}, changed => $Change->{ChangeTime},
        description => $Change->{DescriptionPlain}, justification => $Change->{JustificationPlain},
        cab_agents => [ map { Login($_) } sort { $a <=> $b } @{ $CAB->{CABAgents} || [] } ],
        cab_customers => [ sort @{ $CAB->{CABCustomers} || [] } ],
        workorder_count => $Change->{WorkOrderCount} + 0,
        workorders => \@WorkOrders, history => \@History,
        dynamic_fields => DynamicFields($Change), links => Links( 'ITSMChange', $ChangeID ),
    };
}

# what the reader's ticket / CI filter must find: changes linked directly or through a work order
my %ChangeOfWO;
sub ChangeOf {
    my ($WOID) = @_;
    $ChangeOfWO{$WOID} //= $WOObject->WorkOrderGet( WorkOrderID => $WOID, UserID => 1 )->{ChangeID};
    return $ChangeOfWO{$WOID} + 0;
}
sub LinkedChanges {
    my ( $Object, $Key ) = @_;
    my %Found;
    for my $L ( @{ Links( $Object, $Key ) } ) {
        $Found{ $L->[1] + 0 } = 1 if $L->[0] eq 'ITSMChange';
        $Found{ ChangeOf( $L->[1] ) } = 1 if $L->[0] eq 'ITSMWorkOrder';
    }
    return [ sort { $a <=> $b } keys %Found ];
}
my $Input = $JSON->Decode( Data => '__INPUT__' );
Emit({
    ids     => [ map { $_ + 0 } @{$IDs} ],
    changes => \%Out,
    pointed => \%Pointed,
    by_ticket => { map { $_ => LinkedChanges( 'Ticket', $_ ) } @{ $Input->{tickets} } },
    by_ci     => { map { $_ => LinkedChanges( 'ITSMConfigItem', $_ ) } @{ $Input->{cis} } },
});
"""


def znuny_json(body: str) -> Any:
    """Run Perl *body* in the peer (after the prelude) and decode what it ``Emit``ted."""
    out = znuny_perl_eval(_PRELUDE + body)
    match = re.search(r"GOLDEN_JSON_BEGIN\n(.*?)\nGOLDEN_JSON_END", out, re.S)
    assert match, f"no result block in peer output: {out[-2000:]!r}"
    return json.loads(match.group(1))


def znuny_sync() -> None:
    """One run of the TiqoraSync add-on in the peer (what the daemon cron task does)."""
    znuny_perl_eval(
        "use lib qw(/opt/otrs /opt/otrs/Kernel/cpan-lib /opt/otrs/Custom);"
        "use Kernel::System::ObjectManager;"
        "local $Kernel::OM = Kernel::System::ObjectManager->new("
        "'Kernel::System::Log' => { LogPrefix => 'golden-itsm-change-sync' });"
        "$Kernel::OM->Get('Kernel::System::TiqoraSync')->Run() or die 'TiqoraSync->Run failed';"
    )


def _znuny_changes(seed: dict[str, Any]) -> dict[str, Any]:
    payload = json.dumps(
        {"tickets": [seed["ticket_id"]], "cis": [c["id"] for c in seed["cis"].values()]}
    )
    result: dict[str, Any] = znuny_json(_ZNUNY_CHANGES.replace("__INPUT__", payload))
    return result


# --- module state: snapshot of the tiqora tables, cleanup of what the module creates -----------


def _tiqora_tables(conn: pymysql.Connection) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE()"
            " AND table_type = 'BASE TABLE' AND table_name LIKE 'tiqora\\_%%'"
            " AND table_name <> 'tiqora_alembic_version' ORDER BY table_name"
        )
        return [str(r[0]) for r in cur.fetchall()]


def _rows(conn: pymysql.Connection, table: str) -> tuple[list[str], list[tuple[Any, ...]]]:
    with conn.cursor() as cur:
        cur.execute(f"SELECT * FROM `{table}`")  # noqa: S608 - names from information_schema
        return [d[0] for d in cur.description], list(cur.fetchall())


def _digest(rows: list[tuple[Any, ...]]) -> str:
    return hashlib.sha256("\n".join(sorted(repr(r) for r in rows)).encode()).hexdigest()


@dataclass
class ModuleState:
    dsn: dict[str, Any]
    snapshot: dict[str, tuple[list[str], list[tuple[Any, ...]]]] = field(default_factory=dict)
    #: Znuny tickets created by the module (removed with ``TicketDelete``)
    tickets: list[int] = field(default_factory=list)
    #: setup rows that existed before the module ran (never deleted)
    had_types: set[str] = field(default_factory=set)
    had_fields: set[str] = field(default_factory=set)

    def connect(self) -> pymysql.Connection:
        return pymysql.connect(**self.dsn)

    def set_setting(self, key: str, value: str | None) -> None:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM tiqora_settings WHERE `key` = %s", (key,))
            if value is not None:
                cur.execute(
                    "INSERT INTO tiqora_settings (`key`, `value`) VALUES (%s, %s)", (key, value)
                )
        clear_detect_cache()

    def restore_tiqora(self) -> list[str]:
        """Put back every ``tiqora_*`` table whose rows differ from the snapshot."""
        restored: list[str] = []
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("SET FOREIGN_KEY_CHECKS=0")
            try:
                for table in _tiqora_tables(conn):
                    cols, now = _rows(conn, table)
                    before = self.snapshot.get(table, (cols, []))
                    if _digest(now) == _digest(before[1]):
                        continue
                    restored.append(table)
                    cur.execute(f"DELETE FROM `{table}`")  # noqa: S608
                    if before[1]:
                        names = ", ".join(f"`{c}`" for c in before[0])
                        marks = ", ".join(["%s"] * len(before[0]))
                        cur.executemany(
                            f"INSERT INTO `{table}` ({names}) VALUES ({marks})",  # noqa: S608
                            before[1],
                        )
            finally:
                cur.execute("SET FOREIGN_KEY_CHECKS=1")
        return restored


_DELETE_TICKETS = r"""
my @IDs = ( __IDS__ );
push @IDs, $TicketObject->TicketSearch(
    Result => 'ARRAY', Title => '__PREFIX__*', UserID => 1, Limit => 1000,
    StateType => [ 'new', 'open', 'closed', 'removed', 'pending reminder', 'pending auto' ],
);
my %Seen;
my $Deleted = 0;
for my $ID ( grep { !$Seen{$_}++ } @IDs ) {
    $TicketObject->TicketDelete( TicketID => $ID, UserID => 1 ) or die "TicketDelete $ID failed";
    $Deleted++;
}
Emit({ deleted => $Deleted });
"""


def _delete_tickets(ids: list[int]) -> int:
    body = _DELETE_TICKETS.replace("__IDS__", ",".join(str(int(i)) for i in ids)).replace(
        "__PREFIX__", TICKET_PREFIX
    )
    return int(znuny_json(body)["deleted"])


def _remove_setup_rows(state: ModuleState) -> None:
    """Delete the change setup rows the module created (never rows that existed before)."""
    fields = [n for n in CHANGE_FIELDS if n not in state.had_fields]
    if fields:
        znuny_json(
            "my $DFObject = $Kernel::OM->Get('Kernel::System::DynamicField');"
            "for my $Name ("
            + ",".join(f"'{n}'" for n in fields)
            + r""") {
    my $DF = $DFObject->DynamicFieldGet( Name => $Name );
    next if !$DF || !$DF->{ID};
    $DFObject->DynamicFieldDelete( ID => $DF->{ID}, UserID => 1 ) or die "DynamicFieldDelete $Name failed";
}
Emit({});"""
        )
    types = [t for t in CHANGE_TYPES if t not in state.had_types]
    if types:
        with state.connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM ticket_type WHERE name IN %s", (tuple(types),))


@pytest.fixture(scope="module")
def itsm_seed(golden_peer_id: str) -> dict[str, Any]:
    """The seed of ``itsm_seed.pl`` (idempotent), including the three ``chg94`` changes."""
    znuny_copy_in(SEED_SCRIPT, SEED_REMOTE)
    out = znuny_perl_eval(f"do '{SEED_REMOTE}' or die $@ || $!;")
    match = re.search(r"GOLDEN_ITSM_SEED_BEGIN\n(.*?)\nGOLDEN_ITSM_SEED_END", out, re.S)
    assert match, f"seed produced no result block: {out[-2000:]!r}"
    seed: dict[str, Any] = json.loads(match.group(1))
    assert set(seed["changes"]["changes"]) == {"mail", "firmware", "database"}
    return seed


@pytest.fixture(scope="module")
def module_state(
    golden_pymysql_dsn: dict[str, Any], itsm_seed: dict[str, Any]
) -> Generator[ModuleState, None, None]:
    state = ModuleState(dict(golden_pymysql_dsn))
    _delete_tickets([])  # leftovers of an aborted run
    with state.connect() as conn:
        for table in _tiqora_tables(conn):
            state.snapshot[table] = _rows(conn, table)
        with conn.cursor() as cur:
            cur.execute("SELECT name FROM ticket_type WHERE name IN %s", (CHANGE_TYPES,))
            state.had_types = {str(r[0]) for r in cur.fetchall()}
            cur.execute("SELECT name FROM dynamic_field WHERE name IN %s", (CHANGE_FIELDS,))
            state.had_fields = {str(r[0]) for r in cur.fetchall()}
    # The ITSM packages create none of these rows: present means an aborted run of this module,
    # which would also turn test_itsm_mode_a::test_setup_on_itsm_peer_is_noop red.
    assert not state.had_types and not state.had_fields, (
        f"change setup rows left by an aborted run: {sorted(state.had_types | state.had_fields)};"
        " remove them (DynamicFieldDelete, DELETE FROM ticket_type) before running the module"
    )
    znuny_copy_in(TIQORASYNC_MODULE, TIQORASYNC_PEER_PATH)
    # TiqoraSync starts at the current end of the signal table (older signals are not ours)
    with state.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT COALESCE(MAX(id), 0) FROM tiqora_cache_invalidation")
        fetched = cur.fetchone()
        start = int(fetched[0]) if fetched else 0
    state.set_setting("tiqorasync.watermark", str(start))
    clear_detect_cache()
    try:
        yield state
    finally:
        # each step runs even when an earlier one fails
        try:
            state.restore_tiqora()  # tiqora rows first: they name the tickets by id
        finally:
            try:
                _delete_tickets(state.tickets)
            finally:
                try:
                    _remove_setup_rows(state)
                finally:
                    znuny_perl_eval(f"unlink '{TIQORASYNC_PEER_PATH}'; print 'ok';")
                    znuny_console("Maint::Cache::Delete")
                    clear_detect_cache()


@pytest.fixture()
def mode_a(module_state: ModuleState) -> ModuleState:
    """Mode A (``znuny_readthrough``) stored explicitly, like the site after its first start."""
    module_state.set_setting(SETTING_MODE, "znuny_readthrough")
    return module_state


@pytest.fixture(scope="module", autouse=True)
def _no_erasures(golden_pymysql_dsn: dict[str, Any]) -> None:
    """The comparison runs without GDPR erasures (no tombstone, nothing redacted)."""
    with pymysql.connect(**golden_pymysql_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM tiqora_itsm_legacy_erased")
        fetched = cur.fetchone()
    assert fetched is not None and int(fetched[0]) == 0, "tombstones present: run on a clean peer"


# --- normalising both sides ----------------------------------------------------------------


def _time(value: Any) -> str | None:
    """Znuny's time text: ``""``/NULL/the sentinel are "not set"."""
    if value in (None, ""):
        return None
    text = str(value)[:19]
    return None if text.startswith("9999-01-01") else text


def _shown(value: datetime | None) -> str | None:
    return None if value is None else value.strftime("%Y-%m-%d %H:%M:%S")


def _plain(value: Any) -> str | None:
    """``*Plain`` text stripped (S3); empty is ``None``."""
    return (str(value) if value is not None else "").strip() or None


def _hidden(history_type: str, value: str | None) -> str | None:
    """The SHA-256 the Perl side sends for ``*NotificationSent`` content (S5)."""
    if not value or not history_type.endswith("NotificationSent"):
        return value
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def _link_key(
    other: str, key: str, link_type: str, direction: str | None, wo: int, pointed: bool
) -> tuple[str, str, str, str | None, int]:
    return other, key, link_type, direction if pointed else None, wo


def _znuny_links(znuny: dict[str, Any], pointed: dict[str, int]) -> list[tuple[Any, ...]]:
    out: list[tuple[Any, ...]] = []
    for wo, links in [(0, znuny["links"])] + [
        (w["number"], w["links"]) for w in znuny["workorders"]
    ]:
        for other, key, link_type, side in links:
            is_pointed = bool(int(pointed[link_type]))
            if not is_pointed:
                assert side == "Source", (other, key, link_type, side)  # LinkList for unpointed
            # LinkList names the OTHER end's role; the reader stores this object's side
            direction = "source" if side == "Target" else "target"
            out.append(_link_key(other, str(key), link_type, direction, int(wo), is_pointed))
    return sorted(out, key=repr)


def _tiqora_links(links: tuple[LegacyLink, ...], pointed: dict[str, int]) -> list[tuple[Any, ...]]:
    return sorted(
        (
            _link_key(
                li.object,
                li.key,
                li.link_type,
                li.direction,
                li.workorder_number,
                bool(int(pointed[li.link_type])),
            )
            for li in links
        ),
        key=repr,
    )


_CAB_FIELDS = (mapping.CAB_AGENTS, mapping.CAB_CUSTOMERS)


def _expected_change(z: dict[str, Any]) -> dict[str, Any]:
    return {
        "number": z["number"],
        "title": z["title"],
        "state": z["state"],
        "category": z["category"],
        "impact": z["impact"],
        "priority": z["priority"],
        "manager_login": z["manager"],
        "builder_login": z["builder"],
        "requested_time": _time(z["requested_time"]),
        "planned_start": _time(z["planned_start"]),
        "planned_end": _time(z["planned_end"]),
        "actual_start": _time(z["actual_start"]),
        "actual_end": _time(z["actual_end"]),
        "created_at": _time(z["created"]),
        "changed_at": _time(z["changed"]),
        "description": _plain(z["description"]),
        "justification": _plain(z["justification"]),
        "cab": [("user", login) for login in z["cab_agents"]]
        + [("customer_user", c) for c in z["cab_customers"]],
        "dynamic_fields": {k: list(v) for k, v in z["dynamic_fields"].items()},
        "workorders": [
            {
                "number": int(w["number"]),
                "title": w["title"],
                "type": w["type"],
                "state": w["state"],
                "agent": w["agent"],
                "planned_start": _time(w["planned_start"]),
                "planned_end": _time(w["planned_end"]),
                "actual_start": _time(w["actual_start"]),
                "actual_end": _time(w["actual_end"]),
                "planned_effort": float(w["planned_effort"] or 0),
                "accounted_time": float(w["accounted_time"] or 0),
                "instruction": _plain(w["instruction"]),
                "report": _plain(w["report"]),
                "dynamic_fields": {k: list(v) for k, v in w["dynamic_fields"].items()},
            }
            for w in sorted(z["workorders"], key=lambda w: int(w["number"]))
        ],
        "history": [
            {
                "type": h["type"],
                "field": h["field"] or None,
                "old": h["old"] or "",
                "new": h["new"] or "",
                "old_name": h["old_name"] or "",
                "new_name": h["new_name"] or "",
                "created": _time(h["created"]),
                "login": h["login"],
                "workorder": int(h["workorder"]),
            }
            for h in z["history"]
        ],
    }


def _cab_display(field_name: str | None, value: str | None) -> str:
    """The reader joins CAB lists with ``", "``, Znuny's history screen with ``","``."""
    if field_name in _CAB_FIELDS and value:
        return ",".join(p.strip() for p in value.split(","))
    return value or ""


def _actual_change(c: LegacyChange) -> dict[str, Any]:
    return {
        "number": c.number,
        "title": c.title,
        "state": c.state,
        "category": c.category,
        "impact": c.impact,
        "priority": c.priority,
        "manager_login": c.manager_login,
        "builder_login": c.builder_login,
        "requested_time": _shown(c.requested_time),
        "planned_start": _shown(c.planned_start),
        "planned_end": _shown(c.planned_end),
        "actual_start": _shown(c.actual_start),
        "actual_end": _shown(c.actual_end),
        "created_at": _shown(c.created_at),
        "changed_at": _shown(c.changed_at),
        "description": c.description,
        "justification": c.justification,
        "cab": list(c.cab),
        "dynamic_fields": {k: list(v) for k, v in c.dynamic_fields.items()},
        "workorders": [
            {
                "number": w.number,
                "title": w.title,
                "type": w.work_order_type,
                "state": w.state,
                "agent": w.agent_login,
                "planned_start": _shown(w.planned_start),
                "planned_end": _shown(w.planned_end),
                "actual_start": _shown(w.actual_start),
                "actual_end": _shown(w.actual_end),
                "planned_effort": w.planned_effort,
                "accounted_time": w.accounted_time,
                "instruction": w.instruction,
                "report": w.report,
                "dynamic_fields": {k: list(v) for k, v in w.dynamic_fields.items()},
            }
            for w in c.workorders
        ],
        "history": [
            {
                "type": h.history_type,
                "field": h.field,
                "old": _hidden(h.history_type, h.old) or "",
                "new": _hidden(h.history_type, h.new) or "",
                "old_name": _cab_display(h.field, _hidden(h.history_type, h.old_display)),
                "new_name": _cab_display(h.field, _hidden(h.history_type, h.new_display)),
                "created": _shown(h.created_at),
                "login": h.user_login,
                "workorder": h.workorder_number,
            }
            for h in c.history
        ],
    }


def _differences(got: Any, want: Any, path: str = "") -> list[str]:
    """Where two normalised structures differ (``path: got != want``), for readable failures."""
    if isinstance(got, dict) and isinstance(want, dict):
        out: list[str] = []
        for key in sorted({*got, *want}, key=str):
            out += _differences(
                got.get(key, "<missing>"), want.get(key, "<missing>"), f"{path}.{key}"
            )
        return out
    if isinstance(got, list) and isinstance(want, list):
        out = [] if len(got) == len(want) else [f"{path}: {len(got)} != {len(want)} items"]
        for index, (g, w) in enumerate(zip(got, want, strict=False)):
            out += _differences(g, w, f"{path}[{index}]")
        return out
    return [] if got == want else [f"{path}: {got!r} != {want!r}"]


# =================================================================================================
# the live reader against Znuny's own API
# =================================================================================================


@pytest.mark.usefixtures("mode_a")
async def test_legacy_reader_equals_znuny_changeget(
    peer: Peer, itsm_seed: dict[str, Any], golden_session_factory: SessionFactory
) -> None:
    """Every change of the peer, read by Tiqora, equals what Znuny's API returns for it."""
    repo = znuny_json(
        r"""
my $Packages = $Kernel::OM->Get('Kernel::System::Package');
Emit({ map { $_->{Name}->{Content} => $_->{Version}->{Content} }
       grep { ( $_->{Status} // '' ) eq 'installed' } $Packages->RepositoryList() });
"""
    )
    assert repo["ITSMChangeManagement"] == peer.change_version
    znuny = _znuny_changes(itsm_seed)
    ids = sorted(int(i) for i in znuny["ids"])
    seeded = {int(c["id"]) for c in itsm_seed["changes"]["changes"].values()}
    assert seeded <= set(ids)
    pointed: dict[str, int] = znuny["pointed"]

    async with golden_session_factory() as session:
        assert await reader.change_tables_present(session)
        assert await reader.iter_change_ids(session, after_id=0, limit=10_000) == ids
        source = ZnunyLiveSource()
        batch = {c.znuny_change_id: c for c in await reader.read_changes(session, ids[:100])}
        for change_id in ids:
            want = znuny["changes"][str(change_id)]
            got = await source.get(session, change_id)
            assert got is not None, change_id
            assert batch.get(change_id, got) == got  # one batch read == one read per change
            diffs = _differences(_actual_change(got), _expected_change(want))
            assert not diffs, (change_id, diffs[:10])
            assert _tiqora_links(got.links, pointed) == _znuny_links(want, pointed), change_id

        # derived times of the seed, spelled out (ruling S2): the mail change has a work order
        # without planned times and two unfinished ones; the database change is complete
        changes = itsm_seed["changes"]["changes"]
        mail = await source.get(session, int(changes["mail"]["id"]))
        database = await source.get(session, int(changes["database"]["id"]))
        assert mail is not None and database is not None
        assert (mail.planned_start, mail.planned_end, mail.actual_end) == (None, None, None)
        assert _shown(mail.actual_start) == "2026-11-02 08:05:00"
        assert [_shown(t) for t in (database.planned_start, database.actual_end)] == [
            "2026-10-05 08:00:00",
            "2026-10-06 09:45:00",
        ]

        # the list: every change, newest first, summary fields as ChangeGet has them
        page, total = await reader.search(
            session, text=None, ticket_id=None, ci_znuny_id=None, limit=100, offset=0
        )
        assert total == len(ids)
        assert sorted(s.znuny_change_id for s in page) == ids[:100]
        for summary in page:
            want = znuny["changes"][str(summary.znuny_change_id)]
            assert (
                summary.number,
                summary.title,
                summary.state,
                _shown(summary.planned_start),
                _shown(summary.planned_end),
                summary.workorder_count,
                _shown(summary.changed_at),
            ) == (
                want["number"],
                want["title"],
                want["state"],
                _time(want["planned_start"]),
                _time(want["planned_end"]),
                int(want["workorder_count"]),
                _time(want["changed"]),
            )
        # the ticket / CI filters find what Znuny's LinkList gives (directly or via work orders)
        for ticket_id, expected in znuny["by_ticket"].items():
            page, total = await reader.search(
                session, text=None, ticket_id=int(ticket_id), ci_znuny_id=None, limit=100, offset=0
            )
            assert sorted(s.znuny_change_id for s in page) == sorted(map(int, expected)), ticket_id
        assert any(znuny["by_ci"].values()), "the seed links work orders to CIs"
        for ci_id, expected in znuny["by_ci"].items():
            page, total = await reader.search(
                session, text=None, ticket_id=None, ci_znuny_id=int(ci_id), limit=100, offset=0
            )
            assert sorted(s.znuny_change_id for s in page) == sorted(map(int, expected)), ci_id
        # text search: the number and the title of every seeded change
        for change_id in sorted(seeded):
            want = znuny["changes"][str(change_id)]
            for needle in (want["number"], want["title"]):
                page, _ = await reader.search(
                    session, text=needle, ticket_id=None, ci_znuny_id=None, limit=100, offset=0
                )
                assert change_id in {s.znuny_change_id for s in page}, needle


# =================================================================================================
# Mode A: the live view writes nothing
# =================================================================================================


@asynccontextmanager
async def _api(
    factory: SessionFactory, user: dict[str, AuthenticatedUser]
) -> AsyncIterator[AsyncClient]:
    async def _db() -> Any:
        async with factory() as session:
            yield session

    app = create_app(SETTINGS)
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: user["u"]
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://tiqora.test"
    ) as client:
        yield client


def _user(uid: int, login: str) -> AuthenticatedUser:
    return AuthenticatedUser(
        id=uid, login=login, first_name="Erika", last_name="Beispiel", auth_method="session"
    )


async def _checksums(factory: SessionFactory) -> dict[str, str]:
    async with factory() as session:
        return await change_checksums(session)


@pytest.mark.usefixtures("mode_a")
async def test_mode_a_change_view_writes_nothing(
    itsm_seed: dict[str, Any], golden_session_factory: SessionFactory
) -> None:
    """List, filters and detail of the live view leave the 20 change tables and
    ``link_relation`` as they were (ordered ``SELECT *``, hashed: ruling F33)."""
    base = "/api/v1/itsm/legacy-changes"
    changes = itsm_seed["changes"]["changes"]
    agent_id = int(itsm_seed["changes"]["agent_id"])
    web01 = int(itsm_seed["cis"]["web01"]["id"])
    before = await _checksums(golden_session_factory)
    assert set(before) == {*reader.CHANGE_TABLES, "link_relation"}

    user = {"u": _user(ROOT, "root@localhost")}
    async with _api(golden_session_factory, user) as client:
        status = await client.get("/api/v1/itsm/changes/status")
        assert status.status_code == 200, status.text
        assert status.json()["legacy"] == "live"

        for login, uid in (("root@localhost", ROOT), ("chg94.agent", agent_id)):
            user["u"] = _user(uid, login)
            listed = await client.get(base, params={"limit": 100})
            assert listed.status_code == 200, listed.text
            assert listed.json()["source"] == "live"
            numbers = {i["znuny_change_id"] for i in listed.json()["items"]}
            assert {int(c["id"]) for c in changes.values()} <= numbers
            found = await client.get(base, params={"q": "chg94 Mail"})
            assert found.status_code == 200, found.text
            assert [i["znuny_change_id"] for i in found.json()["items"]] == [
                int(changes["mail"]["id"])
            ]
            by_ci = await client.get(base, params={"ci": f"znuny:{web01}"})
            assert by_ci.status_code == 200, by_ci.text
            for key in ("mail", "firmware", "database"):
                detail = await client.get(f"{base}/{int(changes[key]['id'])}")
                assert detail.status_code == 200, detail.text
                body = detail.json()
                assert body["source"] == "live" and body["workorders"], key

        # root sees the CI behind the mail change's first work order, the agent (no CMDB group,
        # no queue Raw) sees no linked ticket or CI and only their number
        user["u"] = _user(ROOT, "root@localhost")
        mail = (await client.get(f"{base}/{int(changes['mail']['id'])}")).json()
        assert f"znuny:{web01}" in {c["ref"] for c in mail["cis"]}
        assert int(itsm_seed["ticket_id"]) in {t["ticket_id"] for t in mail["tickets"]}
        by_ticket = await client.get(base, params={"ticket_id": int(itsm_seed["ticket_id"])})
        assert int(changes["mail"]["id"]) in {
            i["znuny_change_id"] for i in by_ticket.json()["items"]
        }
        user["u"] = _user(agent_id, "chg94.agent")
        hidden = (await client.get(f"{base}/{int(changes['mail']['id'])}")).json()
        assert hidden["tickets"] == [] and hidden["cis"] == []
        assert hidden["hidden_links"] >= 2

    assert await _checksums(golden_session_factory) == before


# =================================================================================================
# the archive (Mode B) equals the live reader
# =================================================================================================


async def _noop_progress(done: int, total: int | None, label: str) -> None:
    return None


async def _noop_cancel() -> None:
    return None


async def _archive(factory: SessionFactory) -> ArchiveReport:
    return await archive_changes(
        factory,
        run_id=None,
        dry_run=False,
        page_size=2,  # three changes -> two pages
        progress=_noop_progress,
        check_cancelled=_noop_cancel,
        report=ArchiveReport(),
        settings=SETTINGS,
    )


def _ci_targets(state: ModuleState) -> dict[str, int]:
    """Znuny CI id -> Tiqora CI id of the import map (CIs an earlier import took over)."""
    with state.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT source_id, target_id FROM tiqora_itsm_import_map WHERE source_kind = 'ci'"
            " AND target_id > 0"
        )
        return {str(s): int(t) for s, t in cur.fetchall()}


async def test_archive_equals_reader(
    mode_a: ModuleState, itsm_seed: dict[str, Any], golden_session_factory: SessionFactory
) -> None:
    """Import (``run_import``, step ``changes``), then ``ArchiveSource.get`` ==
    ``ZnunyLiveSource.get`` for every change; the standalone archive afterwards finds every change
    ``unchanged``, and neither writes a Znuny change table.

    The archive stores a CI link as ``tiqora:<id>`` when the import took the CI over and as
    ``znuny:<id>`` otherwise (design I16): that is the only difference allowed. The import takes
    every CI of the peer, so the work-order CI links become ``tiqora:`` refs here.
    """
    before = await _checksums(golden_session_factory)
    report = await run_importer(golden_session_factory, dry_run=False, page_size=2)
    async with golden_session_factory() as session:
        ids = await reader.iter_change_ids(session, after_id=0, limit=10_000)
    step = report.steps["changes"]
    assert ids and step.created + step.updated + step.unchanged == len(ids), step
    assert step.skipped == 0 and step.problems == 0, step
    assert "templates" in {i.object_ref for i in report.issues if i.code == "not_archived"}

    targets = _ci_targets(mode_a)
    live, archive = ZnunyLiveSource(), ArchiveSource()
    async with golden_session_factory() as session:
        ci_keys = 0
        for change_id in ids:
            want = await live.get(session, change_id)
            got = await archive.get(session, change_id)
            assert want is not None and got is not None, change_id
            links = []
            for li in want.links:
                if li.object == reader.CI_OBJECT:
                    assert li.key in targets, f"CI {li.key} was not imported"
                    li = replace(li, key=f"tiqora:{targets[li.key]}")
                    ci_keys += 1
                links.append(li)
            assert got == replace(want, links=tuple(links)), change_id
        assert ci_keys >= 2  # the seed links two work orders to CIs
        page, total = await archive.search(
            session, text="chg94", ticket_id=None, ci_ref=None, limit=100, offset=0
        )
        seeded = {int(c["id"]) for c in itsm_seed["changes"]["changes"].values()}
        assert seeded <= {s.znuny_change_id for s in page} and total >= len(seeded)

    second = await _archive(golden_session_factory)  # the standalone job's code path
    assert (second.created, second.updated, second.unchanged, second.skipped) == (0, 0, len(ids), 0)
    assert second.not_archived.get("templates", 0) >= 1  # the seed's change template
    assert await _checksums(golden_session_factory) == before  # Znuny's change tables untouched


# =================================================================================================
# the change setup rows in Znuny
# =================================================================================================


@pytest.fixture()
async def change_setup(
    module_state: ModuleState, golden_session_factory: SessionFactory
) -> ModuleState:
    """The setup group ``change`` applied (creates only what is missing; removed at module end)."""
    from tiqora.itsm.setup import apply_setup

    async with golden_session_factory() as session:
        plan = await apply_setup(session, ItsmSettings(), groups={"change"}, user_id=ROOT)
        await session.commit()
    assert {i.status for i in plan if i.group == "change"} == {"present"}, plan
    znuny_sync()  # the daemon's TiqoraSync run: Znuny's type / dynamic field caches
    return module_state


_ADMIN_SCREEN = r"""
use CGI;
use Kernel::Modules::AdminDynamicFieldDropdown;
my $DFObject = $Kernel::OM->Get('Kernel::System::DynamicField');
my $Backend  = $Kernel::OM->Get('Kernel::System::DynamicField::Backend');
my $YAML     = $Kernel::OM->Get('Kernel::System::YAML');
my $TypeObject = $Kernel::OM->Get('Kernel::System::Type');
my %Out;
for my $Name ( __NAMES__ ) {
    my $DF = $DFObject->DynamicFieldGet( Name => $Name ) or die "DynamicFieldGet $Name failed";
    my $Raw = Rows( SQL => 'SELECT config FROM dynamic_field WHERE name = ?', Bind => [ \$Name ] )->[0]->[0];
    my @Keys = sort keys %{ $DF->{Config}->{PossibleValues} || {} };

    # the admin screen "change dynamic field" of the agent interface
    $Kernel::OM->ObjectsDiscard( Objects => [ 'Kernel::System::Web::Request', 'Kernel::Output::HTML::Layout' ] );
    $Kernel::OM->ObjectParamAdd(
        'Kernel::System::Web::Request' => { WebRequest => CGI->new("Subaction=Change;ObjectType=Ticket;FieldType=Dropdown;ID=$DF->{ID}") },
        'Kernel::Output::HTML::Layout' => { UserID => 1, UserLogin => 'root@localhost', Lang => 'en', Action => 'AdminDynamicFieldDropdown' },
    );
    $Kernel::OM->Get('Kernel::Output::HTML::Layout');
    my $Screen = Kernel::Modules::AdminDynamicFieldDropdown->new(
        UserID => 1, Action => 'AdminDynamicFieldDropdown', Subaction => 'Change',
    )->Run() // '';

    $Out{$Name} = {
        id => $DF->{ID} + 0, label => $DF->{Label}, field_type => $DF->{FieldType},
        object_type => $DF->{ObjectType}, internal => $DF->{InternalField} + 0, valid => $DF->{ValidID} + 0,
        config => $DF->{Config}, yaml => $YAML->Load( Data => $Raw ),
        possible => [ sort keys %{ $Backend->PossibleValuesGet( DynamicFieldConfig => $DF ) || {} } ],
        accepted => { map { $_ => ( $Backend->FieldValueValidate( DynamicFieldConfig => $DF, Value => $_, UserID => 1 ) ? 1 : 0 ) } @Keys, 'chg94-bogus' },
        labels => { map { $_ => $Backend->ValueLookup( DynamicFieldConfig => $DF, Key => $_ ) } @Keys },
        screen => $Screen,
    };
}
my $List = $DFObject->DynamicFieldListGet( ObjectType => 'Ticket', Valid => 1 ) || [];
Emit({
    fields => \%Out,
    listed => [ map { $_->{Name} } @{$List} ],
    types  => { map { $_ => ( $TypeObject->TypeLookup( Type => $_ ) // 0 ) + 0 } ( __TYPES__ ) },
});
"""


def _stringified(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _stringified(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_stringified(v) for v in value]
    return "" if value is None else str(value)


async def test_setup_change_dfs_like_znuny_admin(
    change_setup: ModuleState, golden_pymysql_dsn: dict[str, Any]
) -> None:
    """``ITSMChangeType`` / ``ITSMChangePhase`` as Tiqora's setup wrote them load in Znuny:
    ``DynamicFieldGet`` and ``YAML->Load`` give the seed config, the admin screen
    ``AdminDynamicFieldDropdown`` (``Subaction=Change``) renders every value, the backend accepts
    every possible value (``FieldValueValidate``) and refuses another one; the ticket types
    ``Change`` / ``Change::Task`` resolve."""
    names = ",".join(f"'{n}'" for n in CHANGE_FIELDS)
    types = ",".join(f"'{t}'" for t in CHANGE_TYPES)
    znuny = znuny_json(_ADMIN_SCREEN.replace("__NAMES__", names).replace("__TYPES__", types))
    assert all(int(znuny["types"][t]) > 0 for t in CHANGE_TYPES), znuny["types"]
    assert set(CHANGE_FIELDS) <= set(znuny["listed"])
    for seed in TIQORA_CHANGE_DYNAMIC_FIELDS:
        got = znuny["fields"][seed.name]
        assert (got["label"], got["field_type"], got["object_type"]) == (
            seed.label,
            seed.field_type,
            seed.object_type,
        )
        assert (got["internal"], got["valid"]) == (1, 1)
        assert _stringified(got["config"]) == _stringified(seed.config), seed.name
        assert _stringified(got["yaml"]) == _stringified(seed.config), seed.name
        keys = sorted(seed.config["PossibleValues"])
        assert got["possible"] == sorted(["", *keys])  # PossibleNone adds the empty choice
        assert got["accepted"] == {**{k: 1 for k in keys}, "chg94-bogus": 0}
        assert got["labels"] == seed.config["PossibleValues"]
        screen = got["screen"]
        assert f'value="{got["id"]}"' in screen or f"ID={got['id']}" in screen, seed.name
        assert seed.name in screen
        for key, label in seed.config["PossibleValues"].items():
            assert f'value="{key}"' in screen and label in screen, (seed.name, key)

    # the rows in the table as written (YAML config parses the same in Python)
    with pymysql.connect(**golden_pymysql_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT name, config FROM dynamic_field WHERE name IN %s", (tuple(CHANGE_FIELDS),)
        )
        stored = {str(n): c.decode() if isinstance(c, bytes) else c for n, c in cur.fetchall()}
    for seed in TIQORA_CHANGE_DYNAMIC_FIELDS:
        assert _stringified(yaml.safe_load(stored[seed.name])) == _stringified(seed.config)


# =================================================================================================
# change and task tickets in Znuny
# =================================================================================================


_TICKETS = r"""
my $Backend  = $Kernel::OM->Get('Kernel::System::DynamicField::Backend');
my $DFObject = $Kernel::OM->Get('Kernel::System::DynamicField');
my %Out;
for my $ID ( __IDS__ ) {
    my %Ticket = $TicketObject->TicketGet( TicketID => $ID, DynamicFields => 1, UserID => 1 );
    my %Labels;
    for my $Name ( __NAMES__ ) {
        my $Value = $Ticket{"DynamicField_$Name"};
        next if !defined $Value || $Value eq '';
        $Labels{$Name} = $Backend->ValueLookup( DynamicFieldConfig => $DFObject->DynamicFieldGet( Name => $Name ), Key => $Value );
    }
    $Out{$ID} = {
        type => $Ticket{Type}, title => $Ticket{Title}, queue => $Ticket{Queue}, state => $Ticket{State},
        dynamic_fields => DynamicFields( \%Ticket ), labels => \%Labels,
        links => Links( 'Ticket', $ID ),
        history => [ map { $_->{HistoryType} . '|' . $_->{Name} } grep { $_->{HistoryType} eq 'TicketDynamicFieldUpdate' }
            $TicketObject->HistoryGet( TicketID => $ID, UserID => 1 ) ],
    };
}
Emit( \%Out );
"""


async def test_change_ticket_visible_in_znuny(
    change_setup: ModuleState, golden_session_factory: SessionFactory
) -> None:
    """A change and a task created by Tiqora (Mode B) are what Znuny shows: ``TicketGet`` gives
    the ticket types, the change ticket's ``ITSMChangeType`` / ``ITSMChangePhase`` values (with
    their labels and the ``TicketDynamicFieldUpdate`` history), and ``LinkList`` the ``ParentChild``
    link from both tickets (the change is the parent)."""
    from tiqora.itsm.change.service import ChangeCreateIn, create_change
    from tiqora.itsm.change.tasks import TaskIn, add_task
    from tiqora.znuny.sysconfig import SysConfig

    state = change_setup
    state.set_setting(SETTING_MODE, "tiqora")
    with state.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM queue WHERE name = 'Golden'")
        row = cur.fetchone()
    assert row is not None, "queue Golden missing: run just golden-seed"
    queue_id = int(row[0])

    async with golden_session_factory() as session:
        sysconfig = SysConfig(session)
        view, _job = await create_change(
            session,
            SETTINGS,
            sysconfig,
            data=ChangeCreateIn(
                title=f"{TICKET_PREFIX} change", change_type="normal", queue_id=queue_id,
                body="Replace switch sw01.example.org",
            ),
            user_id=ROOT,
            session_factory=golden_session_factory,
        )  # fmt: skip
        change_id = view.record.ticket_id
        task = await add_task(
            session,
            SETTINGS,
            sysconfig,
            change_ticket_id=change_id,
            data=TaskIn(title=f"{TICKET_PREFIX} task"),
            user_id=ROOT,
            session_factory=golden_session_factory,
        )
        await session.commit()
    state.tickets.extend([change_id, task.ticket_id])
    znuny_sync()

    names = ",".join(f"'{n}'" for n in CHANGE_FIELDS)
    znuny = znuny_json(
        _TICKETS.replace("__IDS__", f"{change_id},{task.ticket_id}").replace("__NAMES__", names)
    )
    change, child = znuny[str(change_id)], znuny[str(task.ticket_id)]
    assert (change["type"], change["queue"], change["state"]) == ("Change", "Golden", "new")
    assert child["type"] == "Change::Task"
    assert change["dynamic_fields"] == {"ITSMChangeType": ["normal"], "ITSMChangePhase": ["draft"]}
    assert change["labels"] == {"ITSMChangeType": "Normal", "ITSMChangePhase": "Draft"}
    assert sorted(change["history"]) == sorted(
        [
            "TicketDynamicFieldUpdate|%%FieldName%%ITSMChangeType%%Value%%normal%%OldValue%%",
            "TicketDynamicFieldUpdate|%%FieldName%%ITSMChangePhase%%Value%%draft%%OldValue%%",
        ]
    ), change["history"]
    assert child["dynamic_fields"] == {}  # tasks carry no change fields
    # ParentChild is pointed: from the change the task is the "Target" (child), from the task the
    # change is the "Source" (parent)
    assert [str(task.ticket_id), "ParentChild", "Target"] in [
        [key, lt, side] for other, key, lt, side in change["links"] if other == "Ticket"
    ]
    assert [str(change_id), "ParentChild", "Source"] in [
        [key, lt, side] for other, key, lt, side in child["links"] if other == "Ticket"
    ]
