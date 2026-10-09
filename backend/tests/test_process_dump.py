"""Process/ACL dump builders: Znuny ProcessDump/ACLDump shapes and the checksum.

Seed ids use the 881000-881499 band (rulings F8). The shared session DB may
hold rows of other modules, so assertions look at this module's entities only.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Generator
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.db.legacy.profile import (
    LegacySchemaProfile,
    SchemaProfileId,
    get_legacy_schema_profile,
    set_legacy_schema_profile,
)
from tiqora.deployed.acl_dump import build_acl_dump
from tiqora.process.config import (
    ActivityConfig,
    ActivityDialogConfig,
    ProcessConfig,
    TransitionActionConfig,
    TransitionConfig,
)
from tiqora.process.dump import (
    PROCESS_STATE_NAMES,
    STATE_FADEAWAY,
    build_process_dump,
    canonical_json,
    dump_checksum,
    dump_entity_count,
)

T0 = datetime(2026, 3, 4, 5, 6, 7)
T1 = datetime(2026, 3, 5, 6, 7, 8)

_SEEDED: list[str] = []

_PROCESS_CFG = (
    "Description: Example process\n"
    "StartActivity: Activity-881001\n"
    "StartActivityDialog: ActivityDialog-881001\n"
    "Path:\n  Activity-881001:\n    Transition-881001:\n"
    "      ActivityEntityID: Activity-881002\n"
    "      TransitionAction: [TransitionAction-881001]\n"
)
_TRANSITION_CFG = (
    "Condition:\n  1:\n    Type: and\n    Fields:\n      Queue: {Type: String, Match: Raw}\n"
    "ConditionLinking: and\n"
)


def _to_async_url(sync_url: str) -> str:
    return (
        sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
        .replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
        .replace("postgresql://", "postgresql+asyncpg://", 1)
    )


def _seed_statements() -> tuple[tuple[str, dict[str, Any]], ...]:
    return (
        ("DELETE FROM pm_process WHERE id BETWEEN 881000 AND 881499", {}),
        ("DELETE FROM pm_activity WHERE id BETWEEN 881000 AND 881499", {}),
        ("DELETE FROM pm_activity_dialog WHERE id BETWEEN 881000 AND 881499", {}),
        ("DELETE FROM pm_transition WHERE id BETWEEN 881000 AND 881499", {}),
        ("DELETE FROM pm_transition_action WHERE id BETWEEN 881000 AND 881499", {}),
        ("DELETE FROM acl WHERE id BETWEEN 881000 AND 881499", {}),
        ("DELETE FROM users WHERE id = 881001", {}),
    )


def _entity(conn: Any, table: str, n: int, kind: str, name: str, config: str, **extra: Any) -> None:
    cols = "id, entity_id, name, config, create_time, create_by, change_time, change_by"
    vals = ":id, :eid, :name, :cfg, :t0, 1, :t1, 1"
    params: dict[str, Any] = {
        "id": 881000 + n,
        "eid": f"{kind}-{881000 + n}",
        "name": name,
        "cfg": config,
        "t0": T0,
        "t1": T1,
    }
    if table == "pm_process":
        cols += ", state_entity_id, layout"
        vals += ", :state, ''"
        params["state"] = extra["state"]
    conn.execute(text(f"INSERT INTO {table} ({cols}) VALUES ({vals})"), params)


def _seed(sync_url: str) -> None:
    _SEEDED.append(sync_url)
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        for stmt, params in _seed_statements():
            conn.execute(text(stmt), params)
        _entity(conn, "pm_process", 1, "Process", "Example", _PROCESS_CFG, state=STATE_FADEAWAY)
        _entity(
            conn,
            "pm_activity",
            1,
            "Activity",
            "A1",
            "ActivityDialog:\n  '1': ActivityDialog-881001\n",
        )
        _entity(conn, "pm_activity", 2, "Activity", "A2", "{}\n")
        _entity(conn, "pm_activity_dialog", 1, "ActivityDialog", "D1", "DescriptionShort: Short\n")
        # Znuny sample AgentTicketProcess.yml shapes: an empty dialog hash, RequiredLock '0'.
        _entity(conn, "pm_activity", 3, "Activity", "A3", "ActivityDialog: {}\n")
        _entity(
            conn,
            "pm_activity_dialog",
            2,
            "ActivityDialog",
            "D2",
            "DescriptionShort: '0'\nRequiredLock: '0'\nPermission: '0'\nInterface: []\n",
        )
        _entity(conn, "pm_transition", 1, "Transition", "T1", _TRANSITION_CFG)
        _entity(
            conn,
            "pm_transition_action",
            1,
            "TransitionAction",
            "X1",
            "Module: Kernel::System::ProcessManagement::TransitionAction::TicketStateSet\n"
            "Config:\n  State: open\n",
        )
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (881001, 'dump.agent.881001', 'x', 'Dump', 'Agent', 1, :t, 1, :t, 1)"
            ),
            {"t": T0},
        )
        acl_cols = (
            "id, name, comments, description, valid_id, stop_after_match, config_match,"
            " config_change, create_time, create_by, change_time, change_by"
        )
        for n, name, valid, stop, comment in (
            (1, "b-acl", 1, 1, "line1\nline2"),
            (2, "a'acl", 1, None, None),
            (3, "c", 2, 0, None),
        ):
            conn.execute(
                text(
                    f"INSERT INTO acl ({acl_cols}) VALUES (:id, :name, :comment, '', :valid,"
                    " :stop, :match, :change, :t0, 1, :t1, 881001)"
                ),
                {
                    "id": 881000 + n,
                    "name": name,
                    "comment": comment,
                    "valid": valid,
                    "stop": stop,
                    "match": "Properties:\n  Ticket:\n    Queue: [Raw]\n",
                    "change": "Possible:\n  Ticket:\n    State: [open]\n",
                    "t0": T0,
                    "t1": T1,
                },
            )
    engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def _delete_seeded_rows() -> Generator[None, None, None]:
    yield
    for sync_url in set(_SEEDED):
        engine = create_engine(sync_url)
        with engine.begin() as conn:
            for stmt, params in _seed_statements():
                conn.execute(text(stmt), params)
        engine.dispose()
    _SEEDED.clear()


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def session(request: pytest.FixtureRequest) -> AsyncIterator[AsyncSession]:
    sync_url: str = request.getfixturevalue(request.param)
    _seed(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    async with async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as s:
        yield s
    await engine.dispose()


@pytest.fixture
def no_profile() -> Generator[None, None, None]:
    previous = get_legacy_schema_profile()
    set_legacy_schema_profile(None)
    yield
    set_legacy_schema_profile(previous)


async def test_process_dump_matches_znuny_shape(session: AsyncSession, no_profile: None) -> None:
    dump = await build_process_dump(session)

    p = dump["Process"]["Process-881001"]
    assert p == {
        "ID": 881001,
        "Name": "Example",
        "CreateTime": "2026-03-04 05:06:07",
        "ChangeTime": "2026-03-05 06:07:08",
        "StateEntityID": "S3",
        "State": "FadeAway",
        "StartActivity": "Activity-881001",
        "StartActivityDialog": "ActivityDialog-881001",
        "Path": {
            "Activity-881001": {
                "Transition-881001": {
                    "ActivityEntityID": "Activity-881002",
                    "TransitionAction": ["TransitionAction-881001"],
                }
            }
        },
        "Description": "Example process",
    }
    # DB/Process.pm:1292: an activity without dialogs dumps '' (|| '').
    assert dump["Activity"]["Activity-881002"]["ActivityDialog"] == ""
    assert dump["Activity"]["Activity-881001"]["ActivityDialog"] == {"1": "ActivityDialog-881001"}
    dialog = dump["ActivityDialog"]["ActivityDialog-881001"]
    assert dialog["Interface"] == ""  # Znuny `|| ''`
    assert dialog["Fields"] == {} and dialog["FieldOrder"] == []
    assert dialog["DescriptionShort"] == "Short" and dialog["RequiredLock"] == ""
    assert dump["Transition"]["Transition-881001"]["Condition"] == {
        "1": {"Type": "and", "Fields": {"Queue": {"Type": "String", "Match": "Raw"}}}
    }
    assert dump["Transition"]["Transition-881001"]["ConditionLinking"] == "and"
    action = dump["TransitionAction"]["TransitionAction-881001"]
    assert action["Config"] == {"State": "open"} and action["ID"] == 881001
    assert dump["State"] == {"S1": "Active", "S2": "Inactive", "S3": "FadeAway"}
    assert dump["State"] == PROCESS_STATE_NAMES
    assert dump["ProcessPreferences"] == {}
    assert dump_entity_count(dump) >= 6
    # A dump equals its own JSON round trip, so a stored copy keeps its checksum.
    assert json.loads(canonical_json(dump)) == dump


async def test_checksum_is_stable_and_content_sensitive(
    session: AsyncSession, no_profile: None
) -> None:
    first = dump_checksum(await build_process_dump(session))
    assert first == dump_checksum(await build_process_dump(session))
    assert len(first) == 64

    await session.execute(
        text("UPDATE pm_transition SET config = :c WHERE id = 881001"),
        {"c": _TRANSITION_CFG.replace("Match: Raw", "Match: Junk")},
    )
    await session.commit()
    assert dump_checksum(await build_process_dump(session)) != first


async def test_process_preferences_only_with_the_table(session: AsyncSession) -> None:
    previous = get_legacy_schema_profile()
    profile = LegacySchemaProfile(
        profile_id=SchemaProfileId.ZNUNY_7_2,
        groups_table="permission_groups",
        mail_account_has_oauth=True,
        state_priority_has_color=True,
        junction_tables_have_surrogate_id=True,
        tables=frozenset({"pm_process_preferences"}),
    )
    await session.execute(
        text(
            "CREATE TABLE IF NOT EXISTS pm_process_preferences (process_entity_id VARCHAR(50)"
            " NOT NULL, preferences_key VARCHAR(150) NOT NULL, preferences_value TEXT NULL)"
        )
    )
    await session.execute(
        text(
            "INSERT INTO pm_process_preferences VALUES ('Process-881001', 'Color', 'blue'),"
            " ('Process-881001', 'Empty', NULL)"
        )
    )
    await session.commit()
    try:
        set_legacy_schema_profile(profile)
        with_table = await build_process_dump(session)
        assert with_table["ProcessPreferences"] == {
            "Process-881001": {"Color": "blue", "Empty": ""}
        }
        set_legacy_schema_profile(None)
        assert (await build_process_dump(session))["ProcessPreferences"] == {}
    finally:
        set_legacy_schema_profile(previous)
        await session.execute(text("DROP TABLE pm_process_preferences"))
        await session.commit()


async def test_acl_dump_valid_only_sorted_and_sanitised(session: AsyncSession) -> None:
    dump = await build_acl_dump(session)
    mine = [n for n in dump["ACL"] if n in {"aacl", "b-acl", "c", "a'acl"}]
    assert mine == ["aacl", "b-acl"]  # quote stripped, invalid "c" dropped, sorted

    b = dump["ACL"]["b-acl"]
    assert b["Values"]["StopAfterMatch"] == 1
    assert b["Comment"] == "line1line2"  # ACL.pm:891-894 strips line breaks
    # Logins: ACLGet maps both ids through UserLookup (ACL.pm:393-398); the golden peer run
    # showed ruling F7's "raw ids" premise wrong (tests/golden/test_process_deploy.py).
    assert (b["CreateBy"], b["ChangeBy"]) == ("root@localhost", "dump.agent.881001")
    assert b["ID"] == 881001
    assert b["CreateTime"] == "2026-03-04 05:06:07"
    assert b["Values"]["Properties"] == {"Ticket": {"Queue": ["Raw"]}}
    assert b["Values"]["Possible"] == {"Ticket": {"State": ["open"]}}
    assert b["Values"]["PropertiesDatabase"] == {} and b["Values"]["PossibleNot"] == {}

    a = dump["ACL"]["aacl"]
    assert a["Values"]["StopAfterMatch"] == 0  # NULL -> `|| 0`
    assert a["Comment"] is None


async def test_process_dump_defaults_follow_perl_truthiness(
    session: AsyncSession, no_profile: None
) -> None:
    """``$X || ''`` in DB/Process.pm:1291/1310-1317 is Perl truthiness: the strings ``''`` and
    ``'0'`` fall back to the default, but any reference (even an empty hash or list) is kept.
    Found by the golden peer run (tests/golden/test_process_deploy.py)."""
    dump = await build_process_dump(session)

    assert dump["Activity"]["Activity-881003"]["ActivityDialog"] == {}
    dialog = dump["ActivityDialog"]["ActivityDialog-881002"]
    assert dialog["RequiredLock"] == ""
    assert dialog["Permission"] == ""
    assert dialog["DescriptionShort"] == ""
    assert dialog["Interface"] == []


def test_config_from_dict_accepts_znuny_dump_defaults() -> None:
    assert ActivityConfig.from_dict({"ActivityDialog": ""}).activity_dialogs == {}
    assert (
        TransitionConfig.from_dict({"Condition": {}, "ConditionLinking": ""}).condition_linking
        == "and"
    )
    cfg = ProcessConfig.from_dict({"StartActivity": "", "StartActivityDialog": "", "Path": {}})
    assert cfg.start_activity is None and cfg.start_activity_dialog is None
    assert cfg.path == {}

    dialog = ActivityDialogConfig.from_dict(
        {"Interface": "", "Fields": "", "FieldOrder": "", "RequiredLock": "", "Permission": ""}
    )
    assert dialog.interface == [] and dialog.fields == {} and dialog.field_order == []
    assert dialog.required_lock == "0"
    assert TransitionActionConfig.from_dict({"Module": "", "Config": ""}).config == {}


def test_transition_from_dict_survives_xor_and_fields_shorthand() -> None:
    # Transition.pm:329-333 and :418-428: Condition.Type is the linking fallback,
    # a scalar Fields entry is a String match, a list entry an Array match.
    cfg = TransitionConfig.from_dict(
        {
            "Condition": {
                "Type": "xor",
                "1": {"Type": "xor", "Fields": {"Queue": "Raw", "Tags": ["a", "b"]}},
            }
        }
    )
    assert cfg.condition_linking == "xor"
    assert len(cfg.conditions) == 1
    block = cfg.conditions[0]
    assert block.type_ == "xor"
    assert (block.fields["Queue"].type_, block.fields["Queue"].match) == ("String", "Raw")
    assert (block.fields["Tags"].type_, block.fields["Tags"].match) == ("Array", ["a", "b"])
    explicit = TransitionConfig.from_dict({"Condition": {}, "ConditionLinking": "or"})
    assert explicit.condition_linking == "or"
