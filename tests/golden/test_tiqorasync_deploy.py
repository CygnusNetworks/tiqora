"""Golden-master: TiqoraSync 1.2.0 (heartbeat and deploy on request) against a real Znuny peer.

The peer image does not install the add-on, so the module under test is copied in from
``packages/znuny-addon`` (``docker compose cp``) and driven the way the daemon cron task does it:
``$Kernel::OM->Get('Kernel::System::TiqoraSync')->Run()``. Proves, on the shared golden DB:

- every run writes ``tiqorasync.heartbeat`` (version, capabilities, integer epoch ``at``);
- a ``TiqoraDeploy_process_<n>`` signal makes the add-on do what Znuny's Deploy button does
  (``ProcessDump FILE`` into ``ZZZProcessManagement.pm`` + ``EntitySyncStatePurge``) and report
  ``{"signal_id", "revision_id", "status": "ok"}`` in ``tiqorasync.deploy.process``; the same for
  ACLs (``ZZZACL.pm`` + ``ACLsNeedSyncReset``);
- deploy signals never reach ``Cache->CleanUp``, old-style ``cache_type`` rows still do;
- several signals of one kind lead to one dump for the highest revision.

The golden DB may lack ``tiqora_settings`` / ``tiqora_cache_invalidation``: a module fixture
creates them if absent and drops them again; otherwise it deletes only its own rows and keys.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from _helpers import znuny_copy_in, znuny_perl_eval
from sqlalchemy import bindparam, create_engine, inspect, text
from sqlalchemy.engine import Engine

pytestmark = pytest.mark.golden

# TIQORASYNC_MODULE points at another copy of the module (to show the tests fail on 1.1.1).
MODULE = Path(
    os.environ.get("TIQORASYNC_MODULE")
    or Path(__file__).resolve().parents[2]
    / "packages/znuny-addon/TiqoraSync/Kernel/System/TiqoraSync.pm"
)
PEER_MODULE = "/opt/otrs/Kernel/System/TiqoraSync.pm"
PROCESS_FILE = "/opt/otrs/Kernel/Config/Files/ZZZProcessManagement.pm"
ACL_FILE = "/opt/otrs/Kernel/Config/Files/ZZZACL.pm"
KEYS = (
    "tiqorasync.heartbeat",
    "tiqorasync.deploy.process",
    "tiqorasync.deploy.acl",
    "tiqorasync.watermark",
    "tiqorasync.deploy.process.requested",  # written by Tiqora's queue_deploy_signal
    "tiqorasync.deploy.acl.requested",
)
_BASE = {"signal": 0}  # highest tiqora_cache_invalidation.id before this module ran
ACL_NAME = "golden-tiqorasync-acl"
SYNC_ENTITY = "Activity-golden-tiqorasync"

_PERL_HEAD = r"""
use lib qw(/opt/otrs /opt/otrs/Kernel/cpan-lib /opt/otrs/Custom);
use Kernel::System::ObjectManager;
use JSON::PP;
local $Kernel::OM = Kernel::System::ObjectManager->new(
    'Kernel::System::Log' => {LogPrefix => 'golden'});
"""

# One Run(), with CleanUp and ProcessDump wrapped so the test can see what the add-on called.
_RUN = (
    _PERL_HEAD
    + r"""
require Kernel::System::Cache;
require Kernel::System::ProcessManagement::DB::Process;
my (@CleanUp, $Dumps);
{
    no warnings 'redefine';
    my $CleanUp = \&Kernel::System::Cache::CleanUp;
    *Kernel::System::Cache::CleanUp = sub {
        my ($S, %P) = @_;
        push @CleanUp, $P{Type} // '';
        return $CleanUp->(@_);
    };
    my $Dump = \&Kernel::System::ProcessManagement::DB::Process::ProcessDump;
    *Kernel::System::ProcessManagement::DB::Process::ProcessDump = sub {
        $Dumps++;
        return $Dump->(@_);
    };
}
$Kernel::OM->Get('Kernel::System::TiqoraSync')->Run() or die 'Run failed';
print JSON::PP->new->canonical->encode({cleanup => \@CleanUp, dumps => $Dumps // 0});
"""
)

_ACL_ADD = (
    _PERL_HEAD
    + rf"""
my $ACL = $Kernel::OM->Get('Kernel::System::ACL::DB::ACL');
my $Old = $ACL->ACLGet(Name => '{ACL_NAME}', UserID => 1);
$ACL->ACLDelete(ID => $Old->{{ID}}, UserID => 1) if $Old && $Old->{{ID}};
$ACL->ACLAdd(
    Name => '{ACL_NAME}', Comment => 'Golden ACL', Description => 'golden',
    StopAfterMatch => 1,
    ConfigMatch => {{ Properties => {{ Ticket => {{ Queue => ['Golden'] }} }} }},
    ConfigChange => {{ PossibleNot => {{ Ticket => {{ State => ['closed successful'] }} }} }},
    ValidID => 1, UserID => 1,
) or die 'ACLAdd failed';
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


def _age_file(path: str) -> None:
    """Set the mtime of a deployed file far into the past (or note that it is absent)."""
    out = znuny_perl_eval(
        f"print( -e '{path}' ? ( utime( 1, 1, '{path}' ) ? 'ok' : 'fail' ) : 'ok' );"
    )
    assert out == "ok"


def _mtime(path: str) -> int:
    return int(znuny_perl_eval(f"print( ( stat '{path}' )[9] // 0 );"))


def _file_content(path: str) -> str:
    return znuny_perl_eval(f"local $/; open my $F, '<', '{path}' or die $!; print <$F>;")


@pytest.fixture(scope="module")
def _engine(golden_db_url: str) -> Generator[Engine, None, None]:
    engine = create_engine(golden_db_url)
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def peer(_engine: Engine) -> Generator[Engine, None, None]:
    """Hand-off tables + the add-on module in the peer; everything removed again at teardown."""
    from tiqora.db.tiqora.base import TiqoraBase
    from tiqora.db.tiqora.models import TiqoraCacheInvalidation, TiqoraSettings

    tables = [TiqoraSettings.__table__, TiqoraCacheInvalidation.__table__]
    existing = set(inspect(_engine).get_table_names())
    created = [t.name for t in tables if t.name not in existing]
    TiqoraBase.metadata.create_all(_engine, tables=tables, checkfirst=True)
    with _engine.begin() as conn:
        saved = {
            row[0]: row[1]
            for row in conn.execute(
                text("SELECT `key`, value FROM tiqora_settings WHERE `key` IN :k").bindparams(
                    bindparam("k", expanding=True)
                ),
                {"k": list(KEYS)},
            )
        }
        max_signal = int(
            conn.execute(
                text("SELECT COALESCE(MAX(id), 0) FROM tiqora_cache_invalidation")
            ).scalar_one()
        )
    _BASE["signal"] = max_signal
    znuny_copy_in(MODULE, PEER_MODULE)
    try:
        yield _engine
    finally:
        znuny_perl_eval(_ACL_DROP)
        with _engine.begin() as conn:
            conn.execute(
                text("DELETE FROM pm_entity_sync WHERE entity_id = :e"), {"e": SYNC_ENTITY}
            )
            if "tiqora_cache_invalidation" not in created:
                conn.execute(
                    text("DELETE FROM tiqora_cache_invalidation WHERE id > :m"), {"m": max_signal}
                )
            if "tiqora_settings" not in created:
                for key in KEYS:
                    conn.execute(text("DELETE FROM tiqora_settings WHERE `key` = :k"), {"k": key})
                for key, value in saved.items():
                    conn.execute(
                        text("INSERT INTO tiqora_settings (`key`, value) VALUES (:k, :v)"),
                        {"k": key, "v": value},
                    )
            for name in created:
                conn.execute(text(f"DROP TABLE {name}"))  # noqa: S608
        znuny_perl_eval(f"unlink '{PEER_MODULE}'; print 'ok';")


@pytest.fixture()
def clean(peer: Engine) -> Generator[Engine, None, None]:
    """Fresh state for one test: no add-on keys, no leftover sync rows."""
    with peer.begin() as conn:
        for key in KEYS:
            conn.execute(text("DELETE FROM tiqora_settings WHERE `key` = :k"), {"k": key})
        conn.execute(text("DELETE FROM pm_entity_sync WHERE entity_id = :e"), {"e": SYNC_ENTITY})
        # signals of earlier tests are history: start the watermark after them
        conn.execute(
            text("DELETE FROM tiqora_cache_invalidation WHERE id > :m"), {"m": _BASE["signal"]}
        )
        conn.execute(
            text("INSERT INTO tiqora_settings (`key`, value) VALUES ('tiqorasync.watermark', :v)"),
            {"v": str(_BASE["signal"])},
        )
    yield peer


def _queue(engine: Engine, cache_type: str) -> int:
    with engine.begin() as conn:
        result = conn.execute(
            text(
                "INSERT INTO tiqora_cache_invalidation (ticket_id, cache_type, created)"
                " VALUES (NULL, :c, CURRENT_TIMESTAMP)"
            ),
            {"c": cache_type},
        )
        return int(result.lastrowid)


def _setting(engine: Engine, key: str) -> dict[str, Any] | None:
    with engine.connect() as conn:
        value = conn.execute(
            text("SELECT value FROM tiqora_settings WHERE `key` = :k"), {"k": key}
        ).scalar_one_or_none()
    return None if value is None else json.loads(value)


def _count(engine: Engine, sql: str, **params: object) -> int:
    with engine.connect() as conn:
        return int(conn.execute(text(sql), params).scalar_one())


def _run() -> dict[str, Any]:
    return json.loads(znuny_perl_eval(_RUN))


def _add_sync_row(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO pm_entity_sync (entity_type, entity_id, sync_state, create_time,"
                " change_time) VALUES ('Activity', :e, 'not_sync', CURRENT_TIMESTAMP,"
                " CURRENT_TIMESTAMP)"
            ),
            {"e": SYNC_ENTITY},
        )


def test_run_writes_heartbeat_with_capabilities(clean: Engine) -> None:
    before = int(time.time())
    _run()
    beat = _setting(clean, "tiqorasync.heartbeat")
    assert beat is not None
    assert beat["version"] == "1.2.0"
    assert beat["capabilities"] == ["process_deploy", "acl_deploy"]
    assert isinstance(beat["framework"], str)
    assert beat["framework"].startswith(("6.", "7."))
    # integer epoch seconds (not a formatted time in OTRSTimeZone)
    assert isinstance(beat["at"], int)
    assert before - 5 <= beat["at"] <= int(time.time()) + 5


def test_deploy_signal_dumps_file_and_purges_sync_table(clean: Engine) -> None:
    _add_sync_row(clean)
    assert _count(clean, "SELECT COUNT(*) FROM pm_entity_sync") >= 1
    _age_file(PROCESS_FILE)
    signal_id = _queue(clean, "TiqoraDeploy_process_41")

    result = _run()

    assert _mtime(PROCESS_FILE) > 1_000_000
    assert "package Kernel::Config::Files::ZZZProcessManagement" in _file_content(PROCESS_FILE)
    assert _count(clean, "SELECT COUNT(*) FROM pm_entity_sync") == 0
    status = _setting(clean, "tiqorasync.deploy.process")
    assert status is not None
    assert status["status"] == "ok"
    assert status["revision_id"] == 41
    assert status["signal_id"] == signal_id
    assert status["message"] == ""
    assert isinstance(status["at"], int)
    assert result["dumps"] == 1
    # the watermark moved past the signal
    watermark = _setting_raw(clean, "tiqorasync.watermark")
    assert watermark is not None and int(watermark) >= signal_id
    assert _setting(clean, "tiqorasync.deploy.acl") is None


def _setting_raw(engine: Engine, key: str) -> str | None:
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT value FROM tiqora_settings WHERE `key` = :k"), {"k": key}
        ).scalar_one_or_none()


def test_acl_signal_dumps_acl_file_and_resets_acl_sync(clean: Engine) -> None:
    assert znuny_perl_eval(_ACL_ADD) == "ok"
    assert _count(clean, "SELECT COUNT(*) FROM acl_sync") >= 1
    _age_file(ACL_FILE)
    signal_id = _queue(clean, "TiqoraDeploy_acl_9")

    result = _run()

    assert _mtime(ACL_FILE) > 1_000_000
    assert ACL_NAME in _file_content(ACL_FILE)
    assert _count(clean, "SELECT COUNT(*) FROM acl_sync") == 0
    status = _setting(clean, "tiqorasync.deploy.acl")
    assert status is not None
    assert (status["status"], status["revision_id"], status["signal_id"]) == ("ok", 9, signal_id)
    assert result["dumps"] == 0  # an ACL signal does not dump processes
    assert _setting(clean, "tiqorasync.deploy.process") is None


def test_signal_is_not_passed_to_cache_cleanup(clean: Engine) -> None:
    _queue(clean, "TiqoraDeploy_process_3")
    _queue(clean, "TiqoraDeploy_acl_3")

    result = _run()

    # (other Znuny code calls CleanUp too, so the list is not empty)
    assert not any(t.startswith("TiqoraDeploy_") for t in result["cleanup"])
    assert _setting(clean, "tiqorasync.deploy.process")["status"] == "ok"  # type: ignore[index]
    assert _setting(clean, "tiqorasync.deploy.acl")["status"] == "ok"  # type: ignore[index]


def test_multiple_signals_one_dump_highest_revision(clean: Engine) -> None:
    _queue(clean, "TiqoraDeploy_process_5")
    _queue(clean, "TiqoraDeploy_process_7")
    last = _queue(clean, "TiqoraDeploy_process_6")

    result = _run()

    assert result["dumps"] == 1
    status = _setting(clean, "tiqorasync.deploy.process")
    assert status is not None
    assert status["revision_id"] == 7
    assert status["signal_id"] == last


def test_old_style_cache_type_rows_still_cleaned(clean: Engine) -> None:
    _queue(clean, "Queue")
    _queue(clean, "TiqoraDeploy_process_2")

    result = _run()

    assert "Queue" in result["cleanup"]
    assert not any(t.startswith("TiqoraDeploy_") for t in result["cleanup"])
    assert result["dumps"] == 1


@pytest.fixture()
def revision_tables(clean: Engine) -> Generator[None, None, None]:
    """The process/ACL revision tables for the end-to-end case (created if the DB lacks them)."""
    from tiqora.db.tiqora.base import TiqoraBase
    from tiqora.db.tiqora.models import TiqoraAclRevision, TiqoraProcessRevision
    from tiqora.deployed.store import clear_revision_cache

    names = ("tiqora_process_revision", "tiqora_acl_revision")
    created = [n for n in names if n not in set(inspect(clean).get_table_names())]
    TiqoraBase.metadata.create_all(
        clean,
        tables=[TiqoraProcessRevision.__table__, TiqoraAclRevision.__table__],
        checkfirst=True,
    )
    with clean.begin() as conn:
        max_ids = {
            n: int(conn.execute(text(f"SELECT COALESCE(MAX(id), 0) FROM {n}")).scalar_one())  # noqa: S608
            for n in names
            if n not in created
        }
    clear_revision_cache()
    try:
        yield
    finally:
        clear_revision_cache()
        with clean.begin() as conn:
            for name, max_id in max_ids.items():
                conn.execute(text(f"DELETE FROM {name} WHERE id > :m"), {"m": max_id})  # noqa: S608
            for name in created:
                conn.execute(text(f"DROP TABLE {name}"))


@pytest.mark.asyncio
async def test_tiqora_signal_deploy_end_to_end(
    clean: Engine,
    revision_tables: None,
    golden_session_factory: Any,
) -> None:
    """Task 28: ``deploy_from_tiqora(mode="signal")`` -> TiqoraSync ``Run`` -> Tiqora reads ``ok``.

    Tiqora records the revision and queues the signal (never touching the sync tables); the add-on
    runs Znuny's own dump + purge and reports the signal id back, which is what the admin page
    shows as "Znuny: ok". The heartbeat of the same run is what lets the gate pick the signal mode.
    """
    from tiqora.deployed.store import latest_revision_info
    from tiqora.deployed.watcher import deploy_from_tiqora
    from tiqora.deployed.znuny_sync import addon_can_deploy, read_znuny_sync_status
    from tiqora.jobs.service import utc_now

    _add_sync_row(clean)
    _age_file(PROCESS_FILE)
    async with golden_session_factory() as session:
        result = await deploy_from_tiqora(session, "process", user_id=1, mode="signal")
        newest = await latest_revision_info(session, "process")
        pending = await read_znuny_sync_status(
            session, "process", newest=newest, mode="signal", now=utc_now()
        )
    assert result.action in ("recorded", "unchanged")
    assert pending.state == "pending"
    assert _count(clean, "SELECT COUNT(*) FROM pm_entity_sync WHERE entity_id = :e", e=SYNC_ENTITY)

    _run()

    async with golden_session_factory() as session:
        done = await read_znuny_sync_status(
            session, "process", newest=newest, mode="signal", now=utc_now()
        )
        assert await addon_can_deploy(session, "process", now=utc_now())
    assert (done.state, done.revision_id) == ("ok", result.revision_id)
    assert _mtime(PROCESS_FILE) > 1_000_000
    assert _count(clean, "SELECT COUNT(*) FROM pm_entity_sync") == 0
