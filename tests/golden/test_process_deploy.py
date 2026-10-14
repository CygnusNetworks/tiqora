"""Golden-master: process/ACL deploy and new process tickets, Tiqora vs a real Znuny peer.

Proves on the shared golden DB:

- ``build_process_dump`` equals Znuny's ``ProcessDump(ResultType => 'HASH')`` for the
  sample ``AgentTicketProcess.yml`` (DB/Process.pm:1225-1377), key by key;
- the deploy watcher snapshots what Znuny deployed (``initial``), waits while the
  designer has drafts (``pm_entity_sync``) and records a ``znuny`` revision after the
  next Znuny deploy (``ProcessDump FILE`` + ``EntitySyncStatePurge``,
  AdminProcessManagement.pm:1189-1210);
- the same for ACLs: ``ACLAdd`` writes ``acl_sync`` (ACL/DB/ACL.pm:195-200), the
  watcher waits, ``ACLDump`` + ``ACLsNeedSyncReset`` (AdminACL.pm:354-370) lead to a
  snapshot whose ``Values`` equal the ACL file Znuny wrote;
- ``create_process_ticket`` writes the same history type sequence and dynamic field
  values as Znuny's ``AgentTicketProcess`` new-ticket path (:5104-5290, :5463ff,
  :5967) plus the ``TicketProcessTransitions`` event.

Passed on ``znuny-6.5`` and ``znuny-7.3`` (cached peer image, 2026-10-09).
The two revision tables are created by a module fixture if the golden DB lacks them
and dropped again at teardown; rows this module adds to existing tables are deleted.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Generator
from typing import Any

import pytest
import pytest_asyncio
from _helpers import znuny_perl_eval
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

pytestmark = pytest.mark.golden

PROCESS_ID = "Process-f8194a25ab0ccddefeb4240c281c1f56"
ACTIVITY_START = "Activity-e2cd48a1807511cbe1b8cef2e750a9de"  # "Start order"
ACTIVITY_SHIPPING = "Activity-50235d211de0ea1d0d218f06254dc644"
ACTIVITY_COMPLETE = "Activity-91bb50fff304574fdcf9e84b63242f7a"  # "Ordering complete"
DIALOG_COMPLETED = "ActivityDialog-24f90970400e3c2fc976eaf0f1e08b31"
ACL_NAME = "golden-process-deploy-acl"
ACL_FILE = "/tmp/golden-acl.pm"  # noqa: S108 -- inside the peer container, never loaded by Znuny

_PERL_HEAD = r"""
use lib qw(/opt/otrs /opt/otrs/Kernel/cpan-lib /opt/otrs/Custom);
use Kernel::System::ObjectManager;
use JSON::PP;
local $Kernel::OM = Kernel::System::ObjectManager->new(
    'Kernel::System::Log' => {LogPrefix => 'golden'});
"""

# The sample process uses two dynamic fields that a fresh Znuny lacks; ProcessImport refuses
# to import without them (DB/Process.pm:1523-1545).
_ENSURE_DYNAMIC_FIELDS = r"""
my $DF = $Kernel::OM->Get('Kernel::System::DynamicField');
my %Fields = (
    TestDropdownACLProcess => { FieldType => 'Dropdown', Config => {
        PossibleValues => { a => 'A', b => 'B' }, DefaultValue => '', PossibleNone => 1,
        TranslatableValues => 0 } },
    TestTextZeroProcess => { FieldType => 'Text', Config => { DefaultValue => '' } },
);
my $Order = 900;
for my $Name ( sort keys %Fields ) {
    next if %{ $DF->DynamicFieldGet( Name => $Name ) || {} };
    $DF->DynamicFieldAdd(
        Name => $Name, Label => $Name, FieldOrder => $Order++,
        FieldType => $Fields{$Name}{FieldType}, ObjectType => 'Ticket',
        Config => $Fields{$Name}{Config}, ValidID => 1, UserID => 1,
    ) or die "DynamicFieldAdd $Name failed";
}
"""

# Import + Znuny's deploy (AdminProcessManagement.pm:1189-1210: ProcessDump FILE into
# ZZZProcessManagement.pm, then EntitySyncStatePurge). The FILE is what
# Kernel::System::ProcessManagement::Process reads (Process.pm:111, 173), so the Znuny half
# of the process ticket test sees the process too. Prints the HASH dump.
_IMPORT_AND_DEPLOY = (
    _PERL_HEAD
    + _ENSURE_DYNAMIC_FIELDS
    + r"""
my $P = $Kernel::OM->Get('Kernel::System::ProcessManagement::DB::Process');
my $Yaml = $Kernel::OM->Get('Kernel::System::Main')->FileRead(
    Location => '/opt/otrs/scripts/test/sample/ProcessManagement/AgentTicketProcess.yml',
    Result => 'SCALAR');
my %R = $P->ProcessImport(Content => $$Yaml, OverwriteExistingEntities => 1, UserID => 1);
die $R{Message} if !$R{Success};
$P->ProcessDump(ResultType => 'FILE', UserID => 1,
    Location => '/opt/otrs/Kernel/Config/Files/ZZZProcessManagement.pm') or die 'FILE dump failed';
my $Dump = $P->ProcessDump(ResultType => 'HASH', UserID => 1);
$Kernel::OM->Get('Kernel::System::ProcessManagement::DB::Entity')
    ->EntitySyncStatePurge(UserID => 1);
print JSON::PP->new->canonical->encode($Dump);
"""
)

# What AdminProcessManagementActivity does on save: ActivityUpdate + EntitySyncStateSet.
_DESIGNER_EDIT_ACTIVITY = (
    _PERL_HEAD
    + rf"""
my $A = $Kernel::OM->Get('Kernel::System::ProcessManagement::DB::Activity');
my $Activity = $A->ActivityGet(EntityID => '{ACTIVITY_COMPLETE}', UserID => 1) or die 'no activity';
$A->ActivityUpdate(
    ID => $Activity->{{ID}}, EntityID => $Activity->{{EntityID}},
    Name => 'Ordering complete (golden)', Config => $Activity->{{Config}}, UserID => 1,
) or die 'ActivityUpdate failed';
$Kernel::OM->Get('Kernel::System::ProcessManagement::DB::Entity')->EntitySyncStateSet(
    EntityType => 'Activity', EntityID => '{ACTIVITY_COMPLETE}', SyncState => 'not_sync',
    UserID => 1,
) or die 'EntitySyncStateSet failed';
print 'ok';
"""
)

_DEPLOY_PROCESS_ONLY = (
    _PERL_HEAD
    + r"""
my $P = $Kernel::OM->Get('Kernel::System::ProcessManagement::DB::Process');
$P->ProcessDump(ResultType => 'FILE', UserID => 1,
    Location => '/opt/otrs/Kernel/Config/Files/ZZZProcessManagement.pm') or die 'FILE dump failed';
$Kernel::OM->Get('Kernel::System::ProcessManagement::DB::Entity')
    ->EntitySyncStatePurge(UserID => 1);
print 'ok';
"""
)

_ACL_DROP = (
    _PERL_HEAD
    + rf"""
my $ACL = $Kernel::OM->Get('Kernel::System::ACL::DB::ACL');
my $Old = $ACL->ACLGet(Name => '{ACL_NAME}', UserID => 1);
$ACL->ACLDelete(ID => $Old->{{ID}}, UserID => 1) if $Old && $Old->{{ID}};
$ACL->ACLsNeedSyncReset();
print 'ok';
"""
)

# ACL editor "Save" (AdminACLNew -> ACLAdd, which inserts the acl_sync row itself).
_ACL_ADD = (
    _PERL_HEAD
    + rf"""
my $ACL = $Kernel::OM->Get('Kernel::System::ACL::DB::ACL');
my $ID = $ACL->ACLAdd(
    Name => '{ACL_NAME}', Comment => 'Golden ACL', Description => 'golden',
    StopAfterMatch => 1,
    ConfigMatch => {{ Properties => {{ Ticket => {{ Queue => ['Golden'] }} }} }},
    ConfigChange => {{ PossibleNot => {{ Ticket => {{ State => ['closed successful'] }} }} }},
    ValidID => 1, UserID => 1,
) or die 'ACLAdd failed';
print $ID;
"""
)

# ACL deploy (AdminACL.pm:354-370), then read back what the deployed file defines and the
# ACLListGet metadata ACLDump uses (ACL/DB/ACL.pm:858-905).
_ACL_DEPLOY = (
    _PERL_HEAD
    + rf"""
my $ACL = $Kernel::OM->Get('Kernel::System::ACL::DB::ACL');
$ACL->ACLDump(ResultType => 'FILE', Location => '{ACL_FILE}', UserID => 1) or die 'ACLDump failed';
$ACL->ACLsNeedSyncReset() or die 'ACLsNeedSyncReset failed';
require '{ACL_FILE}';
my %Loaded;
Kernel::Config::Files::ZZZACL::Load('Kernel::Config::Files::ZZZACL', \%Loaded);
my ($Data) = grep {{ $_->{{Name}} eq '{ACL_NAME}' }}
    @{{ $ACL->ACLListGet(UserID => 1, ValidIDs => [1]) }};
print JSON::PP->new->canonical->encode({{
    Values => $Loaded{{TicketAcl}}->{{'{ACL_NAME}'}},
    Meta   => {{ map {{ $_ => $Data->{{$_}} }}
        qw(ID CreateTime ChangeTime CreateBy ChangeBy Comment) }},
}});
"""
)

# AgentTicketProcess new-ticket path for the start dialog "Make order" (Queue, Article,
# DynamicField_TestDropdownACLProcess, Type), as user 1:
#   :5135-5155 _CheckField falls back to Process::Default{State,Lock,Priority};
#   :5162-5180 OwnerID 1 on create, Title = article Subject;
#   :5200-5245 TicketCreate, ProcessTicketProcessSet, ProcessTicketActivitySet;
#   :5262-5290 dynamic fields of the FieldOrder;
#   :5463ff save loop in FieldOrder: Queue (TicketQueueSet, a no-op), Article (AddNote,
#     '%%Note', Internal channel), the dynamic field again (ValueSet no-op), Type
#     (TicketTypeSet, a no-op);
#   transitions run in the TicketProcessTransitions transaction event (ProcessManagement.xml
#   9800-TicketProcessTransitions, Transaction => 1), fired by ObjectsDiscard.
_ZNUNY_PROCESS_TICKET = (
    _PERL_HEAD
    + rf"""
my $Config = $Kernel::OM->Get('Kernel::Config');
my $TicketObject = $Kernel::OM->Get('Kernel::System::Ticket');
my $ProcessObject = $Kernel::OM->Get('Kernel::System::ProcessManagement::Process');
my $DFBackend = $Kernel::OM->Get('Kernel::System::DynamicField::Backend');
my $DFObject = $Kernel::OM->Get('Kernel::System::DynamicField');
my $Start = $ProcessObject->ProcessStartpointGet(ProcessEntityID => '{PROCESS_ID}')
    or die 'process not deployed';
my %TicketParam = (
    Title      => 'Golden process order',
    QueueID    => $Kernel::OM->Get('Kernel::System::Queue')->QueueLookup(Queue => 'Raw'),
    TypeID     => $Kernel::OM->Get('Kernel::System::Type')->TypeLookup(Type => 'Unclassified'),
    StateID    => $Kernel::OM->Get('Kernel::System::State')->StateLookup(
        State => $Config->Get('Process::DefaultState')),
    LockID     => $Kernel::OM->Get('Kernel::System::Lock')->LockLookup(
        Lock => $Config->Get('Process::DefaultLock')),
    PriorityID => $Kernel::OM->Get('Kernel::System::Priority')->PriorityLookup(
        Priority => $Config->Get('Process::DefaultPriority')),
    OwnerID    => 1,
    UserID     => 1,
);
my $TicketID = $TicketObject->TicketCreate(%TicketParam) or die 'TicketCreate failed';
$ProcessObject->ProcessTicketProcessSet(
    ProcessEntityID => '{PROCESS_ID}', TicketID => $TicketID, UserID => 1) or die 'process set';
$ProcessObject->ProcessTicketActivitySet(
    ProcessEntityID => '{PROCESS_ID}', ActivityEntityID => $Start->{{Activity}},
    TicketID => $TicketID, UserID => 1) or die 'activity set';
my $Dropdown = $DFObject->DynamicFieldGet(Name => 'TestDropdownACLProcess');
$DFBackend->ValueSet(DynamicFieldConfig => $Dropdown, ObjectID => $TicketID, Value => 'a',
    UserID => 1) or die 'ValueSet';
$TicketObject->TicketQueueSet(QueueID => $TicketParam{{QueueID}}, TicketID => $TicketID,
    UserID => 1) or die 'TicketQueueSet';
$Kernel::OM->Get('Kernel::System::Ticket::Article')->BackendForChannel(ChannelName => 'Internal')
    ->ArticleCreate(
        TicketID => $TicketID, SenderType => 'agent', IsVisibleForCustomer => 0,
        From => '"Admin OTRS" <root@localhost>', MimeType => 'text/plain', Charset => 'utf-8',
        Subject => 'Golden process order', Body => 'Golden process order body',
        HistoryType => 'AddNote', HistoryComment => '%%Note', UserID => 1,
    ) or die 'ArticleCreate';
$DFBackend->ValueSet(DynamicFieldConfig => $Dropdown, ObjectID => $TicketID, Value => 'a',
    UserID => 1) or die 'ValueSet again';
$TicketObject->TicketTypeSet(TypeID => $TicketParam{{TypeID}}, TicketID => $TicketID,
    UserID => 1) or die 'TicketTypeSet';
$Kernel::OM->ObjectsDiscard();
print $TicketID;
"""
)

_REVISION_TABLES = ("tiqora_process_revision", "tiqora_acl_revision")


def _norm(value: Any) -> Any:
    """JSON::PP emits Perl scalars as numbers or strings depending on their last use (``ID``,
    ``Display``): compare every scalar as a string, keep the structure."""
    if isinstance(value, dict):
        return {str(k): _norm(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_norm(v) for v in value]
    return "" if value is None else str(value)


@pytest.fixture(scope="module")
def _revision_tables(golden_db_url: str) -> Generator[None, None, None]:
    """Ruling F9: the revision tables exist for this module only when the golden DB was
    seeded without them; rows added to tables that were already there are removed."""
    from tiqora.db.tiqora.base import TiqoraBase
    from tiqora.db.tiqora.models import TiqoraAclRevision, TiqoraProcessRevision
    from tiqora.deployed import watcher
    from tiqora.deployed.store import clear_revision_cache

    engine = create_engine(golden_db_url)
    existing = set(inspect(engine).get_table_names())
    created = [t for t in _REVISION_TABLES if t not in existing]
    TiqoraBase.metadata.create_all(
        engine,
        tables=[TiqoraProcessRevision.__table__, TiqoraAclRevision.__table__],
        checkfirst=True,
    )
    with engine.begin() as conn:
        max_ids = {
            t: int(conn.execute(text(f"SELECT COALESCE(MAX(id), 0) FROM {t}")).scalar_one())  # noqa: S608
            for t in _REVISION_TABLES
            if t not in created
        }
    clear_revision_cache()
    watcher._candidate.clear()  # noqa: SLF001 -- in-process two-tick state of earlier runs
    try:
        yield
    finally:
        clear_revision_cache()
        watcher._candidate.clear()  # noqa: SLF001
        znuny_perl_eval(_ACL_DROP)
        with engine.begin() as conn:
            for table, max_id in max_ids.items():
                conn.execute(text(f"DELETE FROM {table} WHERE id > :m"), {"m": max_id})  # noqa: S608
            for table in created:
                conn.execute(text(f"DROP TABLE {table}"))
        engine.dispose()


@pytest_asyncio.fixture()
async def session(
    golden_session_factory: async_sessionmaker[AsyncSession], _revision_tables: None
) -> AsyncIterator[AsyncSession]:
    async with golden_session_factory() as s:
        yield s


def _import_and_deploy() -> dict[str, Any]:
    return json.loads(znuny_perl_eval(_IMPORT_AND_DEPLOY))


async def _tick(session: AsyncSession, kind: str) -> Any:
    from tiqora.deployed.watcher import check_deploy_state

    return await check_deploy_state(session, kind, ownership_active=False)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_tiqora_dump_equals_znuny_process_dump(session: AsyncSession) -> None:
    from tiqora.process.dump import build_process_dump

    znuny = _import_and_deploy()
    tiqora = await build_process_dump(session)

    for kind in ("Process", "Activity", "ActivityDialog", "Transition", "TransitionAction"):
        assert set(tiqora[kind]) == set(znuny[kind]), kind
        for eid, entry in znuny[kind].items():
            assert _norm({k: tiqora[kind][eid][k] for k in entry}) == _norm(entry), (kind, eid)
    assert tiqora["State"] == znuny["State"] == {"S1": "Active", "S2": "Inactive", "S3": "FadeAway"}

    # Concrete values of the sample process, including the two Perl-truthiness cases.
    assert set(znuny["Process"]) == {PROCESS_ID}
    process = tiqora["Process"][PROCESS_ID]
    assert (process["Name"], process["State"], process["StartActivity"]) == (
        "TestProcess",
        "Active",
        ACTIVITY_START,
    )
    assert process["Description"] == "Customer selenium ticket process"
    assert tiqora["Activity"][ACTIVITY_COMPLETE]["ActivityDialog"] == {}  # `{} || ''` keeps {}
    assert tiqora["ActivityDialog"][DIALOG_COMPLETED]["RequiredLock"] == ""  # `'0' || ''`
    assert tiqora["ActivityDialog"][DIALOG_COMPLETED]["FieldOrder"] == [
        "Priority",
        "Article",
        "DynamicField_TestTextZeroProcess",
    ]
    assert len(tiqora["Transition"]) == 3 and len(tiqora["TransitionAction"]) == 3


@pytest.mark.asyncio
async def test_watcher_snapshots_after_znuny_deploy(session: AsyncSession) -> None:
    from tiqora.deployed.store import latest_revision_info, load_active_revision
    from tiqora.deployed.sync_tables import pending_sync_count
    from tiqora.process.dump import build_process_dump, dump_checksum

    _import_and_deploy()
    assert await pending_sync_count(session, "process") == 0
    before = await latest_revision_info(session, "process")
    await session.commit()

    # First sighting: no revision -> "initial" at once; else the two-tick confirmation.
    outcomes = [await _tick(session, "process")]
    if before is not None:
        assert outcomes[0].action == "confirming"
        outcomes.append(await _tick(session, "process"))
    assert outcomes[-1].action == "snapshot"
    first = await latest_revision_info(session, "process")
    assert first is not None and first.id == outcomes[-1].revision_id
    assert first.source == ("initial" if before is None else "znuny")
    assert first.checksum == dump_checksum(await build_process_dump(session))
    await session.commit()

    # Designer edit in Znuny: a draft, the running revision stays.
    assert znuny_perl_eval(_DESIGNER_EDIT_ACTIVITY) == "ok"
    waiting = await _tick(session, "process")
    assert (waiting.action, waiting.revision_id, waiting.pending_changes) == (
        "waiting_for_deploy",
        first.id,
        1,
    )

    # Znuny deploy: confirmed on the second tick, then recorded as a "znuny" revision.
    assert znuny_perl_eval(_DEPLOY_PROCESS_ONLY) == "ok"
    confirm = await _tick(session, "process")
    assert (confirm.action, confirm.revision_id) == ("confirming", first.id)
    snap = await _tick(session, "process")
    assert snap.action == "snapshot" and snap.revision_id is not None
    assert snap.revision_id > first.id
    latest = await latest_revision_info(session, "process")
    assert latest is not None and (latest.id, latest.source) == (snap.revision_id, "znuny")
    assert latest.checksum == dump_checksum(await build_process_dump(session))
    loaded = await load_active_revision(session, "process")
    assert loaded is not None
    assert loaded.dump["Activity"][ACTIVITY_COMPLETE]["Name"] == "Ordering complete (golden)"
    assert (await _tick(session, "process")).action == "unchanged"


@pytest.mark.asyncio
async def test_acl_deploy_cycle(session: AsyncSession) -> None:
    from tiqora.deployed.acl_dump import build_acl_dump
    from tiqora.deployed.store import latest_revision_info, load_active_revision
    from tiqora.process.dump import dump_checksum

    assert znuny_perl_eval(_ACL_DROP) == "ok"
    before = await latest_revision_info(session, "acl")
    await session.commit()

    acl_id = int(znuny_perl_eval(_ACL_ADD).strip())
    sync_rows = (
        await session.execute(
            text("SELECT acl_id, sync_state FROM acl_sync"),
        )
    ).all()
    assert [(str(r[0]), r[1]) for r in sync_rows] == [(str(acl_id), "not_sync")]
    await session.commit()
    pending = await _tick(session, "acl")
    assert (pending.action, pending.pending_changes) == (
        "running_live" if before is None else "waiting_for_deploy",
        1,
    )

    peer = json.loads(znuny_perl_eval(_ACL_DEPLOY))
    outcomes = [await _tick(session, "acl")]
    if before is not None:
        assert outcomes[0].action == "confirming"  # two-tick confirmation
        outcomes.append(await _tick(session, "acl"))
    assert outcomes[-1].action == "snapshot"
    latest = await latest_revision_info(session, "acl")
    assert latest is not None and latest.id == outcomes[-1].revision_id
    assert latest.source == ("initial" if before is None else "znuny")
    dump = await build_acl_dump(session)
    assert latest.checksum == dump_checksum(dump)

    loaded = await load_active_revision(session, "acl")
    assert loaded is not None
    entry = loaded.dump["ACL"][ACL_NAME]
    # Values equal the deployed ZZZACL file; metadata equals ACLListGet (user logins, see below).
    assert _norm(entry["Values"]) == _norm(peer["Values"])
    assert entry["Values"] == {
        "StopAfterMatch": 1,
        "Properties": {"Ticket": {"Queue": ["Golden"]}},
        "PropertiesDatabase": {},
        "Possible": {},
        "PossibleAdd": {},
        "PossibleNot": {"Ticket": {"State": ["closed successful"]}},
    }
    meta = peer["Meta"]
    assert _norm({k: entry[k] for k in meta}) == _norm(meta)
    # ACLGet maps both user ids to logins (ACL/DB/ACL.pm:393-398), not raw ids as ruling F7
    # assumed.
    assert (entry["ID"], entry["CreateBy"], entry["ChangeBy"], entry["Comment"]) == (
        acl_id,
        "root@localhost",
        "root@localhost",
        "Golden ACL",
    )


_NUMBER_RE = re.compile(r"\d+")


async def _history(session: AsyncSession, ticket_id: int) -> list[tuple[str, str]]:
    """``(history type, name)`` with ticket-specific numbers (TN, ticket id) masked."""
    rows = await session.execute(
        text(
            "SELECT t.name, h.name FROM ticket_history h"
            " JOIN ticket_history_type t ON t.id = h.history_type_id"
            " WHERE h.ticket_id = :id ORDER BY h.id"
        ),
        {"id": ticket_id},
    )
    return [(r[0], _NUMBER_RE.sub("#", r[1])) for r in rows.all()]


async def _ticket_facts(session: AsyncSession, ticket_id: int) -> dict[str, Any]:
    row = (
        await session.execute(
            text(
                "SELECT t.title, q.name, s.name, p.name, l.name, ty.name, t.user_id"
                " FROM ticket t JOIN queue q ON q.id = t.queue_id"
                " JOIN ticket_state s ON s.id = t.ticket_state_id"
                " JOIN ticket_priority p ON p.id = t.ticket_priority_id"
                " JOIN ticket_lock_type l ON l.id = t.ticket_lock_id"
                " JOIN ticket_type ty ON ty.id = t.type_id WHERE t.id = :id"
            ),
            {"id": ticket_id},
        )
    ).one()
    dfs = await session.execute(
        text(
            "SELECT f.name, v.value_text FROM dynamic_field_value v"
            " JOIN dynamic_field f ON f.id = v.field_id"
            " WHERE v.object_id = :id AND f.object_type = 'Ticket' ORDER BY f.name"
        ),
        {"id": ticket_id},
    )
    return {
        "ticket": tuple(row),
        "dynamic_fields": {name: value for name, value in dfs.all()},
    }


@pytest.mark.asyncio
async def test_new_process_ticket_matches_znuny_history(
    golden_session_factory: async_sessionmaker[AsyncSession], session: AsyncSession
) -> None:
    from tiqora.process.actor import ProcessActor
    from tiqora.process.process_ticket import create_process_ticket
    from tiqora.znuny.sysconfig import SysConfig

    _import_and_deploy()
    znuny_ticket_id = int(znuny_perl_eval(_ZNUNY_PROCESS_TICKET).strip())

    async with golden_session_factory() as write:
        result = await create_process_ticket(
            write,
            golden_session_factory,
            SysConfig(write),
            actor=ProcessActor.agent(1),
            process_entity_id=PROCESS_ID,
            field_values={
                "Queue": "Raw",
                "Type": "Unclassified",
                "Article": {"Subject": "Golden process order", "Body": "Golden process order body"},
                "DynamicField_TestDropdownACLProcess": "a",
            },
        )
        await write.commit()
    assert result.transition.activity_changed is True
    assert result.transition.new_activity_entity_id == ACTIVITY_SHIPPING

    znuny_history = await _history(session, znuny_ticket_id)
    tiqora_history = await _history(session, result.ticket_id)
    assert [t for t, _ in tiqora_history] == [t for t, _ in znuny_history], (
        f"history_type sequence differs:\nznuny:  {znuny_history}\ntiqora: {tiqora_history}"
    )
    assert tiqora_history == znuny_history
    # Ticket.pm::TicketCreate writes ServiceUpdate + SLAUpdate (Ticket::Service) and TypeUpdate
    # (Ticket::Type) right after NewTicket; the ITSM peers switch both on, the plain peers not.
    sysconfig = SysConfig(session)
    create_rows = (
        ["ServiceUpdate", "SLAUpdate"] if await sysconfig.get_bool("Ticket::Service") else []
    ) + (["TypeUpdate"] if await sysconfig.get_bool("Ticket::Type") else [])
    off = len(create_rows)
    assert [t for t, _ in znuny_history] == [
        "NewTicket",
        *create_rows,
        "TicketDynamicFieldUpdate",  # ProcessManagementProcessID
        "TicketDynamicFieldUpdate",  # ProcessManagementActivityID (start)
        "TicketDynamicFieldUpdate",  # TestDropdownACLProcess
        "AddNote",
        "Misc",  # "Reset of unlock time." -- the agent article on an unlocked ticket
        "TicketDynamicFieldUpdate",  # transition "Approved" -> Shipping
        "StateUpdate",  # its TicketStateSet action: new -> open
        "SetPendingTime",  # the non-pending state resets the pending time
    ]
    assert znuny_history[4 + off] == ("AddNote", "%%Note")  # AgentTicketProcess.pm:5650-5651
    assert znuny_history[6 + off] == (
        "TicketDynamicFieldUpdate",
        _NUMBER_RE.sub(
            "#",
            "%%FieldName%%ProcessManagementActivityID"
            f"%%Value%%{ACTIVITY_SHIPPING}%%OldValue%%{ACTIVITY_START}",
        ),
    )

    znuny_facts = await _ticket_facts(session, znuny_ticket_id)
    tiqora_facts = await _ticket_facts(session, result.ticket_id)
    assert tiqora_facts == znuny_facts
    assert znuny_facts == {
        "ticket": ("Golden process order", "Raw", "open", "3 normal", "unlock", "Unclassified", 1),
        "dynamic_fields": {
            "ProcessManagementActivityID": ACTIVITY_SHIPPING,
            "ProcessManagementProcessID": PROCESS_ID,
            "TestDropdownACLProcess": "a",
        },
    }
