"""Golden-master: the process designer (Tiqora's ``pm_*`` writer, import/export, deploy through
TiqoraSync, attachments, appointments, ``Module`` conditions) against a real Znuny peer.

Runs on the shared golden DB with ``GOLDEN=1`` (``znuny-6.5`` and ``znuny-7.3``, see
``README-multi-peer.md``). Cases:

- ``test_znuny_export_imports_into_tiqora_and_round_trips``: a Znuny export (sample file ->
  ``ProcessImport`` -> ``_GetProcessData`` + ``YAML->Dump``, AdminProcessManagement.pm:2063-2129)
  is imported by Tiqora (new ids), exported again, and equal modulo ids/times/list order;
- ``test_tiqora_draft_is_a_draft_in_znuny``: a ``ProcessWriter`` edit is ``not_sync`` in Znuny and
  becomes readable once TiqoraSync has drained the cache signal;
- ``test_tiqora_deploy_runs_in_znuny``: a ``signal`` deploy ends in ``ZZZProcessManagement.pm``
  and in the config Znuny's ``ActivityDialog`` module reads; Tiqora's watcher stays quiet;
- ``test_znuny_reads_tiqora_written_yaml``: everything Tiqora wrote loads with ``YAML->Load`` and
  Znuny's ``ProcessDump(HASH)`` equals ``build_process_dump``;
- ``test_appointment_create_matches_znuny``: ``AppointmentCreate`` in both systems writes the same
  ``calendar_appointment`` columns;
- ``test_module_condition_unknown_module_no_crash_in_znuny``: D2 ``Module.pm`` on the peer;
- ``test_attachment_option_key_resolves_to_same_bytes``: an option key Znuny generated is
  resolved by Tiqora to the same bytes (ruling: FileID order differs from id order).

Peer differences: none recorded yet. Record each 7.3 difference here with the upstream file.

The module fixture creates the ``tiqora_*`` tables the golden DB may lack (settings, cache
signals, revisions) and removes them again; rows it adds to existing tables are deleted, and the
sample process is restored to its imported state.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import AsyncIterator, Generator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from _helpers import znuny_copy_in, znuny_perl_eval
from sqlalchemy import bindparam, create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

pytestmark = pytest.mark.golden

SAMPLE_DIR = "/opt/otrs/scripts/test/sample/ProcessManagement"
SAMPLES = [
    "AgentTicketProcess.yml",
    "CustomerTicketProcess.yml",
    *(f"Complex{n}.yml" for n in range(1, 7)),
]
MODULE = Path(
    os.environ.get("TIQORASYNC_MODULE")
    or Path(__file__).resolve().parents[2]
    / "packages/znuny-addon/TiqoraSync/Kernel/System/TiqoraSync.pm"
)
PEER_MODULE = "/opt/otrs/Kernel/System/TiqoraSync.pm"
PROCESS_FILE = "/opt/otrs/Kernel/Config/Files/ZZZProcessManagement.pm"
PROCESS_ID = "Process-f8194a25ab0ccddefeb4240c281c1f56"
DIALOG_COMPLETED = "ActivityDialog-24f90970400e3c2fc976eaf0f1e08b31"
SYNC_KEYS = (
    "tiqorasync.heartbeat",
    "tiqorasync.deploy.process",
    "tiqorasync.deploy.acl",
    "tiqorasync.watermark",
    "tiqorasync.deploy.process.requested",  # written by Tiqora's queue_deploy_signal
    "tiqorasync.deploy.acl.requested",
)
ENTITY_TYPES = ("Process", "Activity", "ActivityDialog", "Transition", "TransitionAction")
_ID_RE = re.compile(rf"\b(?:{'|'.join(ENTITY_TYPES)})-[0-9a-f]{{32}}\b")
_PM_TABLES = {
    "Process": "pm_process",
    "Activity": "pm_activity",
    "ActivityDialog": "pm_activity_dialog",
    "Transition": "pm_transition",
    "TransitionAction": "pm_transition_action",
}
_TIQORA_TABLES = (
    "tiqora_settings",
    "tiqora_cache_invalidation",
    "tiqora_process_revision",
    "tiqora_acl_revision",
)
CALENDAR_NAME = "golden-designer-calendar"
ATTACHMENT_DF = "ProcessManagementAttachment"

# What the module fixture has to undo.
_STATE: dict[str, Any] = {
    "dfs": set(),  # dynamic fields this module created
    "entity_ids": set(),  # pm_* entities this module created (any type)
    "tickets": set(),
}

_PERL_HEAD = r"""
use lib qw(/opt/otrs /opt/otrs/Kernel/cpan-lib /opt/otrs/Custom);
use Kernel::System::ObjectManager;
use JSON::PP;
use Digest::MD5 qw(md5_hex);
local $Kernel::OM = Kernel::System::ObjectManager->new(
    'Kernel::System::Log' => {LogPrefix => 'golden'});
# Tiqora writes pm_* rows behind Znuny's back: start every script without file-cache entries.
$Kernel::OM->Get('Kernel::System::Cache')->CleanUp();
my $JSON = JSON::PP->new->ascii->canonical;
"""

_ENSURE_DF = r"""
sub EnsureDF {
    my ($Name, @Created) = @_;
    my $DF = $Kernel::OM->Get('Kernel::System::DynamicField');
    return 0 if %{ $DF->DynamicFieldGet( Name => $Name ) || {} };
    $DF->DynamicFieldAdd(
        Name => $Name, Label => $Name, FieldOrder => 950,
        FieldType => 'Text', ObjectType => 'Ticket',
        Config => { DefaultValue => '' }, ValidID => 1, UserID => 1,
    ) or die "DynamicFieldAdd $Name failed";
    return 1;
}
"""

# AdminProcessManagement.pm:2063-2129 (_GetProcessData) + the ProcessExport YAML dump.
_EXPORT_SUB = r"""
sub ExportProcess {
    my (%Param) = @_;
    my $Process = $Kernel::OM->Get('Kernel::System::ProcessManagement::DB::Process')
        ->ProcessGet( ID => $Param{ID}, UserID => 1 ) or die 'ProcessGet failed';
    my %Data = ( Process => $Process );
    for my $A ( @{ $Process->{Activities} } ) {
        my $Activity = $Kernel::OM->Get('Kernel::System::ProcessManagement::DB::Activity')
            ->ActivityGet( EntityID => $A, UserID => 1 );
        $Data{Activities}->{$A} = $Activity;
        for my $D ( @{ $Activity->{ActivityDialogs} } ) {
            $Data{ActivityDialogs}->{$D} = $Kernel::OM
                ->Get('Kernel::System::ProcessManagement::DB::ActivityDialog')
                ->ActivityDialogGet( EntityID => $D, UserID => 1 );
        }
    }
    for my $T ( @{ $Process->{Transitions} } ) {
        $Data{Transitions}->{$T} = $Kernel::OM
            ->Get('Kernel::System::ProcessManagement::DB::Transition')
            ->TransitionGet( EntityID => $T, UserID => 1 );
    }
    for my $TA ( @{ $Process->{TransitionActions} } ) {
        $Data{TransitionActions}->{$TA} = $Kernel::OM
            ->Get('Kernel::System::ProcessManagement::DB::TransitionAction')
            ->TransitionActionGet( EntityID => $TA, UserID => 1 );
    }
    return $Kernel::OM->Get('Kernel::System::YAML')->Dump( Data => \%Data );
}
"""


def _perl_json(code: str) -> Any:
    return json.loads(znuny_perl_eval(code))


def _sample_text(name: str) -> str:
    return znuny_perl_eval(
        f"local $/; open my $F, '<', '{SAMPLE_DIR}/{name}' or die $!; print <$F>;"
    )


def _canon_export(yaml_text: str) -> dict[str, Any]:
    """An export, comparable across two systems: ids become ``Type:Name:fingerprint``, the
    volatile keys (``ID``, ``CreateTime``, ``ChangeTime``) are dropped and the three id lists
    of the process, whose order follows the (random) ids, are sorted."""
    from tiqora.process.designer.yaml_codec import load_znuny_yaml, stringify_scalars

    data = stringify_scalars(load_znuny_yaml(yaml_text))
    sections = {
        "Process": {"p": data["Process"]},
        "Activity": data.get("Activities", {}),
        "ActivityDialog": data.get("ActivityDialogs", {}),
        "Transition": data.get("Transitions", {}),
        "TransitionAction": data.get("TransitionActions", {}),
    }

    def strip_ids(value: Any) -> Any:
        if isinstance(value, dict):
            return {strip_ids(k): strip_ids(v) for k, v in value.items()}
        if isinstance(value, list):
            return [strip_ids(v) for v in value]
        if isinstance(value, str):
            return _ID_RE.sub(lambda m: m.group(0).split("-", 1)[0], value)
        return value

    labels: dict[str, str] = {}
    for etype, entries in sections.items():
        for entry in entries.values():
            fingerprint = hashlib.sha1(  # noqa: S324 -- a label, not a secret
                json.dumps(
                    strip_ids({k: v for k, v in entry.get("Config", {}).items() if k != "Scope"}),
                    sort_keys=True,
                ).encode()
            ).hexdigest()[:8]
            if etype == "Process":  # its Path is keyed by ids: no fingerprint, one process only
                fingerprint = ""
            labels[entry["EntityID"]] = f"{etype}:{entry.get('Name', '')}:{fingerprint}"

    def relabel(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                relabel(k): relabel(v)
                for k, v in value.items()
                if k not in ("ID", "CreateTime", "ChangeTime")
            }
        if isinstance(value, list):
            return [relabel(v) for v in value]
        if isinstance(value, str):
            return _ID_RE.sub(lambda m: labels.get(m.group(0), m.group(0)), value)
        return value

    out = relabel(data)
    for key in ("Activities", "Transitions", "TransitionActions"):
        out["Process"][key] = sorted(out["Process"].get(key, []))
    return out  # type: ignore[no-any-return]


def _assert_same(tiqora: Any, znuny: Any) -> None:
    import difflib

    tiqora, znuny = (
        json.loads(json.dumps(tiqora, default=str)),
        json.loads(json.dumps(znuny, default=str)),
    )
    if tiqora != znuny:
        diff = difflib.unified_diff(
            json.dumps(znuny, indent=1, sort_keys=True).splitlines(),
            json.dumps(tiqora, indent=1, sort_keys=True).splitlines(),
            "znuny",
            "tiqora",
            lineterm="",
            n=2,
        )
        pytest.fail("Tiqora differs from Znuny:\n" + "\n".join(list(diff)[:400]))


def _purge_entities(engine: Engine, *yaml_texts: str) -> None:
    ids = {m.group(0) for text_ in yaml_texts for m in _ID_RE.finditer(text_)}
    with engine.begin() as conn:
        for eid in ids:
            etype = eid.split("-", 1)[0]
            conn.execute(
                text(f"DELETE FROM {_PM_TABLES[etype]} WHERE entity_id = :e"),  # noqa: S608
                {"e": eid},
            )
            conn.execute(text("DELETE FROM pm_entity_sync WHERE entity_id = :e"), {"e": eid})


@pytest.fixture(scope="module")
def _engine(golden_db_url: str) -> Generator[Engine, None, None]:
    engine = create_engine(golden_db_url)
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def peer(_engine: Engine) -> Generator[Engine, None, None]:
    """Hand-off + revision tables, the add-on module in the peer; everything undone at teardown."""
    from tiqora.db.tiqora.base import TiqoraBase
    from tiqora.db.tiqora.models import (
        TiqoraAclRevision,
        TiqoraCacheInvalidation,
        TiqoraProcessRevision,
        TiqoraSettings,
    )
    from tiqora.deployed import watcher
    from tiqora.deployed.store import clear_revision_cache

    models = [
        TiqoraSettings,
        TiqoraCacheInvalidation,
        TiqoraProcessRevision,
        TiqoraAclRevision,
    ]
    tables = [m.__table__ for m in models]
    existing = set(inspect(_engine).get_table_names())
    created = [t for t in _TIQORA_TABLES if t not in existing]
    TiqoraBase.metadata.create_all(_engine, tables=tables, checkfirst=True)
    max_ids: dict[str, int] = {}
    saved: dict[str, str] = {}
    with _engine.begin() as conn:
        for table in (
            "tiqora_cache_invalidation",
            "tiqora_process_revision",
            "tiqora_acl_revision",
        ):
            if table not in created:
                max_ids[table] = int(
                    conn.execute(text(f"SELECT COALESCE(MAX(id), 0) FROM {table}")).scalar_one()  # noqa: S608
                )
        if "tiqora_settings" not in created:
            saved = {
                row[0]: row[1]
                for row in conn.execute(
                    text("SELECT `key`, value FROM tiqora_settings WHERE `key` IN :k").bindparams(
                        bindparam("k", expanding=True)
                    ),
                    {"k": list(SYNC_KEYS)},
                )
            }
        conn.execute(text("DELETE FROM tiqora_settings WHERE `key` IN ('tiqorasync.heartbeat')"))
    clear_revision_cache()
    watcher._candidate.clear()  # noqa: SLF001 -- in-process two-tick state of earlier runs
    znuny_copy_in(MODULE, PEER_MODULE)
    try:
        yield _engine
    finally:
        clear_revision_cache()
        watcher._candidate.clear()  # noqa: SLF001
        _cleanup(_engine, created, max_ids, saved)


def _cleanup(
    engine: Engine, created: list[str], max_ids: dict[str, int], saved: dict[str, str]
) -> None:
    for ticket_id in sorted(_STATE["tickets"]):
        znuny_perl_eval(
            _PERL_HEAD + f"$Kernel::OM->Get('Kernel::System::Ticket')->TicketDelete("
            f"TicketID => {ticket_id}, UserID => 1); print 'ok';"
        )
    with engine.begin() as conn:
        for eid in _STATE["entity_ids"]:
            etype = eid.split("-", 1)[0]
            conn.execute(
                text(f"DELETE FROM {_PM_TABLES[etype]} WHERE entity_id = :e"),  # noqa: S608
                {"e": eid},
            )
        conn.execute(text("DELETE FROM pm_entity_sync"))  # the sample process is re-imported below
        conn.execute(
            text(
                "UPDATE calendar_appointment SET parent_id = NULL WHERE calendar_id ="
                " (SELECT id FROM calendar WHERE name = :n)"
            ),
            {"n": CALENDAR_NAME},
        )
        conn.execute(
            text(
                "DELETE FROM calendar_appointment WHERE calendar_id ="
                " (SELECT id FROM calendar WHERE name = :n)"
            ),
            {"n": CALENDAR_NAME},
        )
        conn.execute(text("DELETE FROM calendar WHERE name = :n"), {"n": CALENDAR_NAME})
        for name in _STATE["dfs"]:
            conn.execute(
                text(
                    "DELETE FROM dynamic_field_value WHERE field_id ="
                    " (SELECT id FROM dynamic_field WHERE name = :n)"
                ),
                {"n": name},
            )
            conn.execute(text("DELETE FROM dynamic_field WHERE name = :n"), {"n": name})
        for table, max_id in max_ids.items():
            conn.execute(text(f"DELETE FROM {table} WHERE id > :m"), {"m": max_id})  # noqa: S608
        if "tiqora_settings" not in created:
            for key in SYNC_KEYS:
                conn.execute(text("DELETE FROM tiqora_settings WHERE `key` = :k"), {"k": key})
            for key, value in saved.items():
                conn.execute(
                    text("INSERT INTO tiqora_settings (`key`, value) VALUES (:k, :v)"),
                    {"k": key, "v": value},
                )
        for name in created:
            conn.execute(text(f"DROP TABLE {name}"))  # noqa: S608
    znuny_perl_eval(f"unlink '{PEER_MODULE}'; print 'ok';")


@pytest_asyncio.fixture()
async def session(
    golden_session_factory: async_sessionmaker[AsyncSession], peer: Engine
) -> AsyncIterator[AsyncSession]:
    async with golden_session_factory() as s:
        yield s


# --- 1. Znuny export -> Tiqora import -> Tiqora export ---------------------------------------


def _znuny_import_and_export(sample: str, name: str, dynamic_fields: list[str]) -> dict[str, Any]:
    df_list = " ".join(sorted(set(dynamic_fields)))
    out = znuny_perl_eval(
        _PERL_HEAD
        + _ENSURE_DF
        + _EXPORT_SUB
        + rf"""
my $Yaml = $Kernel::OM->Get('Kernel::System::YAML');
my $Raw = $Kernel::OM->Get('Kernel::System::Main')->FileRead(
    Location => '{SAMPLE_DIR}/{sample}', Result => 'SCALAR');
my $Data = $Yaml->Load( Data => $$Raw );
$Data->{{Process}}->{{Name}} = '{name}';
my @Created;
for my $Field (qw({df_list})) {{ push @Created, $Field if EnsureDF($Field); }}
my $P = $Kernel::OM->Get('Kernel::System::ProcessManagement::DB::Process');
my %R = $P->ProcessImport(
    Content => $Yaml->Dump( Data => $Data ), OverwriteExistingEntities => 0, UserID => 1);
my %Out = (
    success => $R{{Success}} ? 1 : 0, message => $R{{Message}} // '',
    created_dfs => \@Created,
);
if ( $R{{Success}} ) {{
    my ($Proc) = grep {{ $_->{{Name}} eq '{name}' }} @{{ $P->ProcessListGet( UserID => 1 ) }};
    $Out{{yaml}} = ExportProcess( ID => $Proc->{{ID}} );
}}
print $JSON->encode( \%Out );
"""
    )
    return json.loads(out)  # type: ignore[no-any-return]


@pytest.mark.asyncio
@pytest.mark.parametrize("sample", SAMPLES)
async def test_znuny_export_imports_into_tiqora_and_round_trips(
    peer: Engine, session: AsyncSession, sample: str
) -> None:
    from tiqora.process.designer.exchange import export_process_yaml, import_process_yaml
    from tiqora.process.designer.writer import ProcessWriter

    name = f"golden-designer-{sample.removesuffix('.yml').lower()}"
    dynamic_fields = re.findall(r"DynamicField_(\w+)", _sample_text(sample))
    znuny = _znuny_import_and_export(sample, name, dynamic_fields)
    _STATE["dfs"].update(znuny["created_dfs"])
    if not znuny["success"]:
        pytest.skip(f"the peer refuses to import {sample}: {znuny['message']}")
    znuny_yaml: str = znuny["yaml"]
    _STATE["entity_ids"].update(m.group(0) for m in _ID_RE.finditer(znuny_yaml))

    out = await import_process_yaml(
        session, ProcessWriter(session, user_id=1), znuny_yaml, overwrite_existing=False
    )
    await session.commit()
    _, tiqora_yaml = await export_process_yaml(session, out.process_entity_id)
    _STATE["entity_ids"].update(m.group(0) for m in _ID_RE.finditer(tiqora_yaml))

    assert not (set(_ID_RE.findall(znuny_yaml)) & set(_ID_RE.findall(tiqora_yaml)))  # new ids
    _assert_same(_canon_export(tiqora_yaml), _canon_export(znuny_yaml))
    # the imported process is a draft (Deviation 2 of the plan), every entity has a sync row
    with peer.connect() as conn:
        drafts = int(conn.execute(text("SELECT COUNT(*) FROM pm_entity_sync")).scalar_one())
    assert drafts >= 1
    _purge_entities(peer, znuny_yaml, tiqora_yaml)


# --- 2./3. draft and deploy -------------------------------------------------------------------

_IMPORT_SAMPLE = (
    _PERL_HEAD
    + _ENSURE_DF
    + r"""
for my $Field (qw(TestDropdownACLProcess TestTextZeroProcess)) { EnsureDF($Field); }
my $P = $Kernel::OM->Get('Kernel::System::ProcessManagement::DB::Process');
my $Raw = $Kernel::OM->Get('Kernel::System::Main')->FileRead(
    Location => '/opt/otrs/scripts/test/sample/ProcessManagement/AgentTicketProcess.yml',
    Result => 'SCALAR');
my %R = $P->ProcessImport(Content => $$Raw, OverwriteExistingEntities => 1, UserID => 1);
die $R{Message} if !$R{Success};
$P->ProcessDump(ResultType => 'FILE', UserID => 1,
    Location => '/opt/otrs/Kernel/Config/Files/ZZZProcessManagement.pm') or die 'FILE dump failed';
$Kernel::OM->Get('Kernel::System::ProcessManagement::DB::Entity')
    ->EntitySyncStatePurge(UserID => 1);
# fill the file cache the way a running system has it
$Kernel::OM->Get('Kernel::System::ProcessManagement::DB::ActivityDialog')
    ->ActivityDialogGet(
        EntityID => 'ActivityDialog-24f90970400e3c2fc976eaf0f1e08b31', UserID => 1);
print 'ok';
"""
)

_RUN_SYNC = (
    _PERL_HEAD.replace("$Kernel::OM->Get('Kernel::System::Cache')->CleanUp();", "")
    + r"""
$Kernel::OM->Get('Kernel::System::TiqoraSync')->Run() or die 'Run failed';
print 'ok';
"""
)

_ZNUNY_VIEW = (
    _PERL_HEAD.replace("$Kernel::OM->Get('Kernel::System::Cache')->CleanUp();", "")
    + rf"""
my $Entity = $Kernel::OM->Get('Kernel::System::ProcessManagement::DB::Entity');
my %Sync = map {{ $_->{{EntityID}} => $_->{{SyncState}} }}
    @{{ $Entity->EntitySyncStateList( UserID => 1 ) || [] }};
my $Dialog = $Kernel::OM->Get('Kernel::System::ProcessManagement::DB::ActivityDialog')
    ->ActivityDialogGet( EntityID => '{DIALOG_COMPLETED}', UserID => 1 );
print $JSON->encode({{ sync => \%Sync, short => $Dialog->{{Config}}->{{DescriptionShort}} }});
"""
)

# The config Znuny reads while it runs (ZZZProcessManagement.pm), not the DB.
_ZNUNY_LIVE_DIALOG = (
    _PERL_HEAD.replace("$Kernel::OM->Get('Kernel::System::Cache')->CleanUp();", "")
    + rf"""
my $Dialog = $Kernel::OM->Get('Kernel::System::ProcessManagement::ActivityDialog')
    ->ActivityDialogGet( ActivityDialogEntityID => '{DIALOG_COMPLETED}', Interface => 'all' );
print $JSON->encode({{ short => $Dialog->{{DescriptionShort}} }});
"""
)


async def _edit_completed_dialog(session: AsyncSession, short: str) -> None:
    from tiqora.process.designer.draft import load_entity
    from tiqora.process.designer.writer import ProcessWriter

    entity = await load_entity(session, "ActivityDialog", DIALOG_COMPLETED)
    config = {**entity.config, "DescriptionShort": short}
    await ProcessWriter(session, user_id=1).update_entity(
        "ActivityDialog",
        DIALOG_COMPLETED,
        expected_version=entity.version,
        name=entity.name,
        config=config,
    )
    await session.commit()


@pytest.fixture()
def sample_process(peer: Engine) -> Generator[Engine, None, None]:
    """The sample process, imported and deployed in Znuny, no drafts, no tiqora hand-off state."""
    assert znuny_perl_eval(_IMPORT_SAMPLE) == "ok"
    with peer.begin() as conn:
        conn.execute(
            text("DELETE FROM tiqora_settings WHERE `key` IN :k").bindparams(
                bindparam("k", expanding=True)
            ),
            {"k": list(SYNC_KEYS)},
        )
        max_signal = int(
            conn.execute(
                text("SELECT COALESCE(MAX(id), 0) FROM tiqora_cache_invalidation")
            ).scalar_one()
        )
        conn.execute(
            text("INSERT INTO tiqora_settings (`key`, value) VALUES ('tiqorasync.watermark', :v)"),
            {"v": str(max_signal)},
        )
    yield peer
    assert znuny_perl_eval(_IMPORT_SAMPLE) == "ok"  # back to the shipped sample state


@pytest.mark.asyncio
async def test_tiqora_draft_is_a_draft_in_znuny(
    sample_process: Engine, session: AsyncSession
) -> None:
    before = json.loads(znuny_perl_eval(_ZNUNY_VIEW))
    assert before["sync"].get(DIALOG_COMPLETED) is None
    original = before["short"]

    await _edit_completed_dialog(session, "golden draft")

    # Znuny's own list shows the entity as not deployed, without TiqoraSync having run
    after_write = json.loads(znuny_perl_eval(_ZNUNY_VIEW))
    assert after_write["sync"][DIALOG_COMPLETED] == "not_sync"
    # the cache signal is drained by TiqoraSync, then Znuny's ActivityDialogGet has the new text
    assert znuny_perl_eval(_RUN_SYNC) == "ok"
    drained = json.loads(znuny_perl_eval(_ZNUNY_VIEW))
    assert drained["short"] == "golden draft"
    assert drained["short"] != original
    assert drained["sync"][DIALOG_COMPLETED] == "not_sync"  # still a draft after the drain
    # a draft is not deployed: the config file Znuny runs on is unchanged
    assert json.loads(znuny_perl_eval(_ZNUNY_LIVE_DIALOG))["short"] == original


@pytest.mark.asyncio
async def test_tiqora_deploy_runs_in_znuny(sample_process: Engine, session: AsyncSession) -> None:
    from tiqora.deployed.watcher import check_deploy_state, deploy_from_tiqora

    original = json.loads(znuny_perl_eval(_ZNUNY_LIVE_DIALOG))["short"]
    # the watcher snapshots what Znuny deployed (initial, then confirmed on the second tick)
    for _ in range(3):
        outcome = await check_deploy_state(session, "process", ownership_active=False)
        if outcome.action == "unchanged":
            break
    assert outcome.action == "unchanged"

    await _edit_completed_dialog(session, "golden deployed")
    waiting = await check_deploy_state(session, "process", ownership_active=False)
    assert waiting.action == "waiting_for_deploy"  # drafts are not run

    result = await deploy_from_tiqora(session, "process", user_id=1, mode="signal")
    assert result.action == "recorded"
    with sample_process.connect() as conn:
        # signal mode never clears the sync table: Znuny does that
        assert int(conn.execute(text("SELECT COUNT(*) FROM pm_entity_sync")).scalar_one()) >= 1
    assert json.loads(znuny_perl_eval(_ZNUNY_LIVE_DIALOG))["short"] == original  # not yet

    assert znuny_perl_eval(_RUN_SYNC) == "ok"

    deployed = znuny_perl_eval(
        f"local $/; open my $F, '<', '{PROCESS_FILE}' or die $!; print <$F>;"
    )
    assert "golden deployed" in deployed
    assert json.loads(znuny_perl_eval(_ZNUNY_LIVE_DIALOG))["short"] == "golden deployed"
    with sample_process.connect() as conn:
        assert int(conn.execute(text("SELECT COUNT(*) FROM pm_entity_sync")).scalar_one()) == 0
        status = json.loads(
            conn.execute(
                text("SELECT value FROM tiqora_settings WHERE `key` = 'tiqorasync.deploy.process'")
            ).scalar_one()
        )
    assert status["status"] == "ok"
    assert status["revision_id"] == result.revision_id
    # Tiqora's watcher sees Znuny's deploy as the revision it recorded itself
    after = await check_deploy_state(session, "process", ownership_active=False)
    assert (after.action, after.revision_id) == ("unchanged", result.revision_id)


# --- 4. Znuny reads what Tiqora wrote --------------------------------------------------------

_YAML_LOAD_ALL = (
    _PERL_HEAD.replace("$Kernel::OM->Get('Kernel::System::Cache')->CleanUp();", "")
    + r"""
my $DB = $Kernel::OM->Get('Kernel::System::DB');
my $Yaml = $Kernel::OM->Get('Kernel::System::YAML');
my (@Bad, $Count);
for my $Query (
    ['pm_process', 'config'], ['pm_process', 'layout'], ['pm_activity', 'config'],
    ['pm_activity_dialog', 'config'], ['pm_transition', 'config'],
    ['pm_transition_action', 'config'],
) {
    my ($Table, $Column) = @{$Query};
    $DB->Prepare( SQL => "SELECT entity_id, $Column FROM $Table" );
    my @Rows;
    while ( my @Row = $DB->FetchrowArray() ) { push @Rows, [@Row]; }
    for my $Row (@Rows) {
        $Count++;
        my $Loaded = $Yaml->Load( Data => $Row->[1] );
        push @Bad, "$Table.$Column:$Row->[0]" if ref $Loaded ne 'HASH';
    }
}
print $JSON->encode({ bad => \@Bad, count => $Count });
"""
)

_DUMP_HASH = (
    _PERL_HEAD
    + r"""
my $Dump = $Kernel::OM->Get('Kernel::System::ProcessManagement::DB::Process')
    ->ProcessDump( ResultType => 'HASH', UserID => 1 );
print $JSON->encode($Dump);
"""
)


def _norm(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _norm(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_norm(v) for v in value]
    return "" if value is None else str(value)


@pytest.mark.asyncio
async def test_znuny_reads_tiqora_written_yaml(
    sample_process: Engine, session: AsyncSession
) -> None:
    from tiqora.process.designer.exchange import import_process_yaml
    from tiqora.process.designer.writer import ProcessWriter
    from tiqora.process.dump import build_process_dump

    # Tiqora writes a whole process (new ids, so every pm_* row below is Tiqora's)
    out = await import_process_yaml(
        session,
        ProcessWriter(session, user_id=1),
        _sample_text("AgentTicketProcess.yml").replace(
            "Name: TestProcess", "Name: golden-designer-tiqora-written", 1
        ),
        overwrite_existing=False,
    )
    await session.commit()
    ids = await _process_entity_ids(session, out.process_entity_id)
    _STATE["entity_ids"].update(ids)
    await _edit_completed_dialog(session, "Zeichen: äöü ß – “quoted”")  # non-ASCII scalars

    loaded = json.loads(znuny_perl_eval(_YAML_LOAD_ALL))
    assert loaded["bad"] == []
    assert loaded["count"] >= 6

    znuny = json.loads(znuny_perl_eval(_DUMP_HASH))
    tiqora = await build_process_dump(session)
    for kind in ("Process", "Activity", "ActivityDialog", "Transition", "TransitionAction"):
        assert set(tiqora[kind]) == set(znuny[kind]), kind
        for eid, entry in znuny[kind].items():
            assert _norm({k: tiqora[kind][eid][k] for k in entry}) == _norm(entry), (kind, eid)
    assert out.process_entity_id in znuny["Process"]


async def _process_entity_ids(session: AsyncSession, process_entity_id: str) -> set[str]:
    from tiqora.process.designer.exchange import export_process_yaml

    _, exported = await export_process_yaml(session, process_entity_id)
    return {m.group(0) for m in _ID_RE.finditer(exported)}


# --- 5. AppointmentCreate ---------------------------------------------------------------------

_APPOINTMENT_CASES = [
    pytest.param(
        {
            "Title": "Golden appointment",
            "Description": "Created by a transition action",
            "Location": "Room 1",
            "StartTime": "2026-11-02 09:00:00",
            "EndTime": "2026-11-02 10:30:00",
            "AllDay": "0",
        },
        id="plain",
    ),
    pytest.param(
        {
            "Title": "Golden all-day",
            "StartTime": "2026-11-03 00:00:00",
            "EndTime": "2026-11-04 00:00:00",
            "AllDay": "1",
        },
        id="all-day",
    ),
    pytest.param(
        {
            "Title": "Golden recurring",
            "StartTime": "2026-11-05 14:00:00",
            "EndTime": "2026-11-05 15:00:00",
            "AllDay": "0",  # (left out, Znuny stores NULL and Tiqora always 0/1)
            "Recurring": "1",
            "RecurrenceType": "Daily",
            "RecurrenceInterval": "1",
            "RecurrenceCount": "3",
        },
        id="recurring-daily",
    ),
]
# Znuny expands a series into child rows (``parent_id``/``recur_id``); Tiqora keeps the series
# in one row and expands on read (tiqora.calendar.recurrence). The comparison below is therefore
# against the parent row of the series, the first one Znuny inserts.
_VOLATILE_COLUMNS = {"id", "unique_id", "create_time", "change_time"}


def _znuny_appointment(config: dict[str, str]) -> int:
    pairs = ", ".join(f"{k} => {json.dumps(v)}" for k, v in config.items())
    return int(
        znuny_perl_eval(
            _PERL_HEAD
            + rf"""
my $Group = $Kernel::OM->Get('Kernel::System::Group')->GroupLookup( Group => 'users' ) || 1;
my $Calendar = $Kernel::OM->Get('Kernel::System::Calendar');
my %Cal = $Calendar->CalendarGet( CalendarName => '{CALENDAR_NAME}' );
if ( !%Cal ) {{
    $Calendar->CalendarCreate(
        CalendarName => '{CALENDAR_NAME}', GroupID => $Group, Color => '#3A87AD',
        ValidID => 1, UserID => 1) or die 'CalendarCreate failed';
}}
my $Action = $Kernel::OM
    ->Get('Kernel::System::ProcessManagement::TransitionAction::AppointmentCreate');
$Action->Run(
    UserID => 1, Ticket => {{ TicketID => 1 }}, ProcessEntityID => 'P1', ActivityEntityID => 'A1',
    TransitionEntityID => 'T1', TransitionActionEntityID => 'TA1',
    Config => {{ CalendarName => '{CALENDAR_NAME}', {pairs} }},
) or die 'AppointmentCreate failed';
my $DB = $Kernel::OM->Get('Kernel::System::DB');
$DB->Prepare( SQL => 'SELECT MIN(id) FROM calendar_appointment WHERE title = ?',
    Bind => [ \{json.dumps(config["Title"])} ] );
my ($ID) = $DB->FetchrowArray();
print $ID;
"""
        )
    )


def _appointment_row(engine: Engine, appointment_id: int) -> dict[str, Any]:
    with engine.connect() as conn:
        row = (
            conn.execute(
                text("SELECT * FROM calendar_appointment WHERE id = :i"), {"i": appointment_id}
            )
            .mappings()
            .one()
        )
    return {k: v for k, v in row.items() if k not in _VOLATILE_COLUMNS}


@pytest.mark.asyncio
@pytest.mark.parametrize("config", _APPOINTMENT_CASES)
async def test_appointment_create_matches_znuny(
    peer: Engine, session: AsyncSession, config: dict[str, str]
) -> None:
    from tiqora.process.actions.appointments import action_appointment_create
    from tiqora.znuny.sysconfig import SysConfig

    znuny_id = _znuny_appointment(config)
    with peer.connect() as conn:
        before = int(
            conn.execute(text("SELECT COALESCE(MAX(id), 0) FROM calendar_appointment")).scalar_one()
        )

    await action_appointment_create(
        session,
        {"CalendarName": CALENDAR_NAME, **config},
        ticket_id=1,
        user_id=1,
        sysconfig=SysConfig(session),
    )
    await session.commit()
    with peer.connect() as conn:
        tiqora_id = int(
            conn.execute(
                text("SELECT MAX(id) FROM calendar_appointment WHERE title = :t AND id > :b"),
                {"t": config["Title"], "b": before - 1 if before else 0},
            ).scalar_one()
        )
    assert tiqora_id != znuny_id

    _assert_same(_appointment_row(peer, tiqora_id), _appointment_row(peer, znuny_id))


# --- 6. Module conditions ---------------------------------------------------------------------


def _znuny_module_validate(match: str, queue: str) -> dict[str, Any]:
    return _perl_json(  # type: ignore[no-any-return]
        _PERL_HEAD
        + rf"""
my $Validator = $Kernel::OM->Get('Kernel::System::ProcessManagement::TransitionValidation::Module')
    or die 'no Module validator';
my $Match = eval {{
    $Validator->Validate(
        Data => {{ TicketID => 1, Queue => '{queue}' }}, FieldName => 'Queue',
        Condition => {{ Type => 'Module', Match => '{match}' }},
        TransitionName => 'Golden', TransitionEntityID => 'T1', ConditionName => 'Condition 1',
        ConditionType => 'and', ConditionLinking => 'and',
    );
}};
print $JSON->encode({{ died => $@ ? 1 : 0, match => $Match ? 1 : 0 }});
"""
    )


@pytest.mark.parametrize(
    ("module", "queue", "expected"),
    [
        ("Kernel::System::ProcessManagement::TransitionValidation::ValidateDemo", "Raw", True),
        ("Kernel::System::ProcessManagement::TransitionValidation::ValidateDemo", "Misc", False),
        ("Kernel::System::ProcessManagement::TransitionValidation::DoesNotExist", "Raw", False),
        ("Not::A::Real::Module", "Raw", False),
    ],
)
def test_module_condition_unknown_module_no_crash_in_znuny(
    peer: Engine, module: str, queue: str, expected: bool
) -> None:
    from tiqora.process.condition_modules import resolve_condition_module
    from tiqora.process.conditions import evaluate_condition_field
    from tiqora.process.config import TransitionConditionField

    znuny = _znuny_module_validate(module, queue)
    assert znuny == {"died": 0, "match": int(expected)}  # unknown module: no match, no crash

    tiqora = evaluate_condition_field(
        "Queue", TransitionConditionField(type_="Module", match=module), {"Queue": queue}
    )
    assert tiqora is expected
    assert (resolve_condition_module(module) is not None) is (module.endswith("::ValidateDemo"))


# --- 7. attachment option keys ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_attachment_option_key_resolves_to_same_bytes(
    peer: Engine, session: AsyncSession
) -> None:
    """Ruling: two files whose filename order differs from their id order. Znuny generates the
    option keys (AgentTicketProcess.pm:6750-6810) and resolves them (TransitionAction/Base.pm
    ``_GetAttachments``, :145-243); Tiqora must list the same keys and resolve them to the same
    bytes."""
    from tiqora.process.attachments_field import (
        collect_reused_attachments,
        list_ticket_attachment_options,
    )
    from tiqora.znuny.sysconfig import SysConfig

    binary = bytes(range(256))
    out = _perl_json(
        _PERL_HEAD
        + _ENSURE_DF
        + rf"""
my $Created = EnsureDF('{ATTACHMENT_DF}');
my $Config = $Kernel::OM->Get('Kernel::Config');
my $Ticket = $Kernel::OM->Get('Kernel::System::Ticket');
my $TicketID = $Ticket->TicketCreate(
    Title => 'Golden attachments', Queue => 'Raw', Lock => 'unlock', Priority => '3 normal',
    State => 'new', OwnerID => 1, UserID => 1) or die 'TicketCreate failed';
my $Backend = $Kernel::OM->Get('Kernel::System::Ticket::Article')
    ->BackendForChannel( ChannelName => 'Internal' );
my $ArticleID = $Backend->ArticleCreate(
    TicketID => $TicketID, SenderType => 'agent', IsVisibleForCustomer => 0,
    From => 'Agent <agent@example.org>', To => 'c@example.org', MimeType => 'text/plain',
    Charset => 'utf-8', Subject => 'Files', Body => 'see attachments',
    HistoryType => 'AddNote', HistoryComment => '%%Note', UserID => 1)
    or die 'ArticleCreate failed';
# written in this order: the second file sorts first by name
my $Zebra = join '', map {{ chr($_) }} 0 .. 255;
$Backend->ArticleWriteAttachment( Content => $Zebra, ContentType => 'application/octet-stream',
    Filename => 'zebra.bin', ArticleID => $ArticleID, UserID => 1 ) or die 'attach 1';
$Backend->ArticleWriteAttachment( Content => "alpha\r\n", ContentType => 'text/plain',
    Filename => 'alpha.txt', ArticleID => $ArticleID, UserID => 1 ) or die 'attach 2';

my $ArticleObject = $Kernel::OM->Get('Kernel::System::Ticket::Article');
my $Format = $Config->Get('Process::DynamicFieldProcessManagementAttachment::FormatString')
    // 'A#%1$d: %2$s';
my (%Options, @Keys);
for my $Article ( $ArticleObject->ArticleList( TicketID => $TicketID ) ) {{
    my %Index = $ArticleObject->ArticleAttachmentIndex(
        TicketID => $TicketID, ArticleID => $Article->{{ArticleID}},
        ExcludePlainText => 1, ExcludeHTMLBody => 1, ExcludeInline => 0);
    for my $FileID ( sort keys %Index ) {{
        my $Key = 'ObjectType=Article;ObjectID=' . $Article->{{ArticleID}} . ';ID=' . $FileID;
        $Options{{$Key}} = sprintf $Format, $Article->{{ArticleNumber}},
            $Index{{$FileID}}->{{Filename}},
            'Artikel', 'Anhang';
        push @Keys, $Key;
    }}
}}
my $DF = $Kernel::OM->Get('Kernel::System::DynamicField')
    ->DynamicFieldGet( Name => '{ATTACHMENT_DF}' );
my $Backend2 = $Kernel::OM->Get('Kernel::System::DynamicField::Backend');
$Backend2->ValueSet( DynamicFieldConfig => $DF, ObjectID => $TicketID,
    Value => join( ',', reverse @Keys ), UserID => 1 );
my $Base = $Kernel::OM
    ->Get('Kernel::System::ProcessManagement::TransitionAction::TicketArticleCreate');
my $Files = $Base->_GetAttachments( Ticket => {{ TicketID => $TicketID }}, Config => {{}} );
my @Resolved = map {{
    {{ filename => $_->{{Filename}}, md5 => md5_hex( $_->{{Content}} ) }}
}} @{{$Files}};
# _GetAttachments cleared the field (Keep is off); select again for Tiqora
$Backend2->ValueSet( DynamicFieldConfig => $DF, ObjectID => $TicketID,
    Value => join( ',', reverse @Keys ), UserID => 1 );
print $JSON->encode({{
    created_df => $Created, ticket_id => $TicketID, keys => \@Keys, options => \%Options,
    resolved => \@Resolved,
}});
"""
    )
    if out["created_df"]:
        _STATE["dfs"].add(ATTACHMENT_DF)
    _STATE["tickets"].add(out["ticket_id"])
    ticket_id = int(out["ticket_id"])

    # the premise of the ruling: name order is not id order
    assert [r["filename"] for r in out["resolved"]] == ["zebra.bin", "alpha.txt"]  # reversed keys
    assert out["keys"] == sorted(out["keys"])
    assert out["options"][out["keys"][0]].endswith("alpha.txt")

    options = await list_ticket_attachment_options(session, ticket_id, SysConfig(session))
    assert {o.value: o.label for o in options} == out["options"]

    files = await collect_reused_attachments(
        session, ticket_id=ticket_id, sysconfig=SysConfig(session)
    )
    await session.commit()
    tiqora = [(name, hashlib.md5(data).hexdigest()) for name, _ctype, data in files]  # noqa: S324
    assert tiqora == [(r["filename"], r["md5"]) for r in out["resolved"]]
    assert dict((r["filename"], r["md5"]) for r in out["resolved"])["zebra.bin"] == (
        hashlib.md5(binary).hexdigest()  # noqa: S324
    )
