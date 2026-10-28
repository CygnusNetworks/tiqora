"""Change-management tables (migration ``20261010_0084``) on MariaDB and PostgreSQL.

The migration builds the tables; the tests compare them with the ORM metadata and exercise the
constraints that matter (cascades from a change, SET NULL on template and CAB, RESTRICT on a CAB
with meetings, unique task template key) and run a downgrade/upgrade round trip. Sync tests on
purpose: Alembic's env.py runs its own event loop. The GDPR test lives in
``test_gdpr_erasure.py`` (it needs that module's helpers). Seed ids stay in the plan band
946100-946199. Leaves the schema at head.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import datetime

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from tests._schema_reset import delete_migration_seed_rows, reset_tiqora_schema
from tests.test_itsm_models import (
    _assert_table_matches_orm,
    _async_url,
    _drop_version_row,
    _had_version_row,
    _migrate,
    _sync_url,
    _tables,
    _version,
)
from tiqora.config import get_settings

pytestmark = pytest.mark.db

_PREVIOUS = "20261010_0083"
_NEW = "20261010_0084"
_NOW = datetime(2026, 10, 27, 8, 0, 0)
_TABLES = (
    "tiqora_itsm_change",
    "tiqora_itsm_change_service",
    "tiqora_itsm_change_impact",
    "tiqora_itsm_change_task",
    "tiqora_itsm_change_template",
    "tiqora_itsm_change_risk_question",
    "tiqora_itsm_change_risk_answer",
    "tiqora_itsm_change_freeze",
    "tiqora_itsm_change_freeze_service",
    "tiqora_itsm_cab",
    "tiqora_itsm_cab_member",
    "tiqora_itsm_cab_meeting",
    "tiqora_itsm_cab_agenda_item",
    "tiqora_itsm_change_event",
    "tiqora_itsm_legacy_change",
    "tiqora_itsm_legacy_change_ref",
    "tiqora_itsm_legacy_erased",
)
_PARAMS = ["mariadb_znuny_url", "postgres_znuny_url"]
_BAND = "BETWEEN 946100 AND 946199"


@pytest.fixture
def alembic_env(request: pytest.FixtureRequest) -> Iterator[str]:
    url = request.getfixturevalue(request.param)
    old = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = _async_url(url)
    get_settings.cache_clear()
    try:
        yield _sync_url(url)
    finally:
        if old is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = old
        get_settings.cache_clear()


@pytest.fixture
def migrated(alembic_env: str) -> Iterator[str]:
    """A schema migrated from scratch to 0084 (so every table is migration-built)."""
    sync_url = alembic_env
    had_version_row = _had_version_row(sync_url)
    reset_tiqora_schema(sync_url)
    try:
        _migrate(_NEW)
        yield sync_url
    finally:
        _migrate("head")
        delete_migration_seed_rows(sync_url)
        if not had_version_row:
            _drop_version_row(sync_url)


def _cleanup(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text(f"DELETE FROM tiqora_itsm_change WHERE ticket_id {_BAND}"))
        conn.execute(text(f"DELETE FROM tiqora_itsm_cab_meeting WHERE id {_BAND}"))
        conn.execute(text(f"DELETE FROM tiqora_itsm_cab WHERE id {_BAND}"))
        conn.execute(text(f"DELETE FROM tiqora_itsm_change_template WHERE id {_BAND}"))
        conn.execute(text(f"DELETE FROM tiqora_itsm_change_freeze WHERE id {_BAND}"))
        conn.execute(text(f"DELETE FROM tiqora_itsm_legacy_change WHERE id {_BAND}"))


def _seed_change(
    conn: object, ticket_id: int, *, template_id: int | None = None, cab_id: int | None = None
) -> None:
    conn.execute(  # type: ignore[attr-defined]
        text(
            "INSERT INTO tiqora_itsm_change (ticket_id, change_type, phase, template_id, cab_id,"
            " created_at, created_by, updated_at, updated_by)"
            " VALUES (:t, 'normal', 'draft', :tpl, :cab, :n, 1, :n, 1)"
        ),
        {"t": ticket_id, "tpl": template_id, "cab": cab_id, "n": _NOW},
    )


def _seed_cab(conn: object, cab_id: int) -> None:
    conn.execute(  # type: ignore[attr-defined]
        text(
            "INSERT INTO tiqora_itsm_cab (id, name, rule, created_at, created_by, updated_at,"
            " updated_by) VALUES (:i, :name, 'any', :n, 1, :n, 1)"
        ),
        {"i": cab_id, "name": f"chg94-cab-{cab_id}", "n": _NOW},
    )


def _seed_template(conn: object, template_id: int) -> None:
    conn.execute(  # type: ignore[attr-defined]
        text(
            "INSERT INTO tiqora_itsm_change_template (id, name, change_type, defaults, tasks,"
            " created_at, created_by, updated_at, updated_by)"
            " VALUES (:i, :name, 'standard', '{}', '[]', :n, 1, :n, 1)"
        ),
        {"i": template_id, "name": f"chg94-tpl-{template_id}", "n": _NOW},
    )


def _seed_meeting(conn: object, meeting_id: int, cab_id: int) -> None:
    conn.execute(  # type: ignore[attr-defined]
        text(
            "INSERT INTO tiqora_itsm_cab_meeting (id, cab_id, title, starts_at, ends_at, status,"
            " created_at, created_by, updated_at, updated_by)"
            " VALUES (:i, :c, 'chg94 meeting', :n, :n, 'planned', :n, 1, :n, 1)"
        ),
        {"i": meeting_id, "c": cab_id, "n": _NOW},
    )


_TASK_SQL = text(
    "INSERT INTO tiqora_itsm_change_task (ticket_id, change_ticket_id, position, task_kind,"
    " template_task_key, created_at, created_by) VALUES (:task, :chg, 1, 'workorder', :key, :n, 1)"
)


def _seed_tree(conn: object, ticket_id: int) -> None:
    """A change with a row in every table that hangs off it."""
    cab_id = ticket_id
    _seed_cab(conn, cab_id)
    _seed_meeting(conn, ticket_id, cab_id)
    _seed_change(conn, ticket_id)
    conn.execute(  # type: ignore[attr-defined]
        text("INSERT INTO tiqora_itsm_change_service (ticket_id, service_id) VALUES (:t, 946101)"),
        {"t": ticket_id},
    )
    conn.execute(  # type: ignore[attr-defined]
        text(
            "INSERT INTO tiqora_itsm_change_impact (ticket_id, kind, ref_key, direct)"
            " VALUES (:t, 'ci', '946101', :d)"
        ),
        {"t": ticket_id, "d": True},
    )
    conn.execute(  # type: ignore[attr-defined]
        text(
            "INSERT INTO tiqora_itsm_change_risk_answer (ticket_id, question_key, answer_key,"
            " points, answered_at, answered_by) VALUES (:t, 'chg94-q', 'a', 1, :n, 1)"
        ),
        {"t": ticket_id, "n": _NOW},
    )
    conn.execute(  # type: ignore[attr-defined]
        text(
            "INSERT INTO tiqora_itsm_change_event (ticket_id, created_at, created_by, event,"
            " from_phase, to_phase) VALUES (:t, :n, 1, 'phase', 'draft', 'submitted')"
        ),
        {"t": ticket_id, "n": _NOW},
    )
    conn.execute(_TASK_SQL, {"task": ticket_id + 50, "chg": ticket_id, "key": "k1", "n": _NOW})  # type: ignore[attr-defined]
    conn.execute(  # type: ignore[attr-defined]
        text(
            "INSERT INTO tiqora_itsm_cab_agenda_item (meeting_id, change_ticket_id, position)"
            " VALUES (:m, :t, 1)"
        ),
        {"m": ticket_id, "t": ticket_id},
    )


_CHILD_COUNTS = (
    ("tiqora_itsm_change_service", "ticket_id"),
    ("tiqora_itsm_change_impact", "ticket_id"),
    ("tiqora_itsm_change_risk_answer", "ticket_id"),
    ("tiqora_itsm_change_event", "ticket_id"),
    ("tiqora_itsm_change_task", "change_ticket_id"),
    ("tiqora_itsm_cab_agenda_item", "change_ticket_id"),
)


def _count(conn: object, table: str, where: str) -> int:
    return int(
        conn.execute(text(f"SELECT COUNT(*) FROM {table} WHERE {where}")).scalar_one()  # type: ignore[attr-defined]
    )


@pytest.mark.parametrize("alembic_env", _PARAMS, indirect=True)
def test_migration_creates_change_tables(migrated: str) -> None:
    sync_url = migrated
    assert _version(sync_url) == _NEW
    assert set(_TABLES) <= _tables(sync_url)
    assert len(_TABLES) == 17
    engine = create_engine(sync_url)
    try:
        for name in _TABLES:
            _assert_table_matches_orm(engine, name)
        insp = inspect(engine)
        indexes = {
            "tiqora_itsm_change": {
                "ix_tiqora_itsm_change_phase": ["phase"],
                "ix_tiqora_itsm_change_window": ["planned_start", "planned_end"],
            },
            "tiqora_itsm_change_impact": {"ix_tiqora_itsm_change_impact_ref": ["kind", "ref_key"]},
            "tiqora_itsm_change_task": {
                "ix_tiqora_itsm_change_task_change": ["change_ticket_id", "position"]
            },
            "tiqora_itsm_change_freeze": {"ix_tiqora_itsm_change_freeze_start": ["starts_at"]},
            "tiqora_itsm_cab_meeting": {"ix_tiqora_itsm_cab_meeting_start": ["starts_at"]},
            "tiqora_itsm_change_event": {
                "ix_tiqora_itsm_change_event_ticket": ["ticket_id", "id"],
                "ix_tiqora_itsm_change_event_time": ["event", "created_at"],
            },
            "tiqora_itsm_legacy_change": {
                "ix_tiqora_itsm_legacy_change_number": ["change_number"],
                "ix_tiqora_itsm_legacy_change_planned": ["planned_start"],
            },
            "tiqora_itsm_legacy_change_ref": {
                "ix_tiqora_itsm_legacy_change_ref": ["ref_kind", "ref_key"]
            },
        }
        for table, wanted in indexes.items():
            got = {
                i["name"]: list(i["column_names"])
                for i in insp.get_indexes(table)
                if not i.get("unique")
            }
            for name, cols in wanted.items():
                assert got.get(name) == cols, (table, name)
        uniques = {
            "tiqora_itsm_change_task": (
                "uq_tiqora_itsm_change_task_tpl",
                ["change_ticket_id", "template_task_key"],
            ),
            "tiqora_itsm_change_template": ("uq_tiqora_itsm_change_template_name", ["name"]),
            "tiqora_itsm_change_risk_question": (
                "uq_tiqora_itsm_change_risk_q_key",
                ["question_key"],
            ),
            "tiqora_itsm_cab": ("uq_tiqora_itsm_cab_name", ["name"]),
            "tiqora_itsm_cab_member": (
                "uq_tiqora_itsm_cab_member",
                ["cab_id", "member_kind", "member_ref"],
            ),
            "tiqora_itsm_cab_agenda_item": (
                "uq_tiqora_itsm_cab_agenda",
                ["meeting_id", "change_ticket_id"],
            ),
            "tiqora_itsm_legacy_erased": (
                "uq_tiqora_itsm_legacy_erased_hash",
                ["identifier_hash"],
            ),
            "tiqora_itsm_legacy_change": (
                "uq_tiqora_itsm_legacy_change_znuny",
                ["znuny_change_id"],
            ),
        }
        for table, (name, cols) in uniques.items():
            found = {
                (u["name"], tuple(u["column_names"])) for u in insp.get_unique_constraints(table)
            } | {
                (i["name"], tuple(i["column_names"]))
                for i in insp.get_indexes(table)
                if i.get("unique")
            }
            assert (name, tuple(cols)) in found, (table, name)
        # foreign keys: only between tiqora_itsm_* tables, with the documented delete rules
        fks = {
            (t, fk["referred_table"], fk["options"].get("ondelete"))
            for t in _TABLES
            for fk in insp.get_foreign_keys(t)
        }
        assert all(ref.startswith("tiqora_itsm_") for _, ref, _ in fks)
        assert ("tiqora_itsm_change", "tiqora_itsm_change_template", "SET NULL") in fks
        assert ("tiqora_itsm_change", "tiqora_itsm_cab", "SET NULL") in fks
        assert ("tiqora_itsm_cab_meeting", "tiqora_itsm_cab", None) in fks
        assert ("tiqora_itsm_cab_member", "tiqora_itsm_cab", "CASCADE") in fks
        # a raw insert that leaves every defaulted column out: the DDL defaults apply
        with engine.begin() as conn:
            _seed_change(conn, 946101)
            row = conn.execute(
                text(
                    "SELECT approval_round, impact_truncated, portal_visible, approval_id,"
                    " impact_requested_at FROM tiqora_itsm_change WHERE ticket_id = 946101"
                )
            ).one()
            assert (row[0], bool(row[1]), bool(row[2]), row[3], row[4]) == (
                0,
                False,
                False,
                None,
                None,
            )
            _seed_cab(conn, 946101)
            cab = conn.execute(
                text("SELECT is_emergency, valid FROM tiqora_itsm_cab WHERE id = 946101")
            ).one()
            assert (bool(cab[0]), bool(cab[1])) == (False, True)
            _seed_template(conn, 946101)
            tpl = conn.execute(
                text("SELECT is_standard, valid FROM tiqora_itsm_change_template WHERE id = 946101")
            ).one()
            assert (bool(tpl[0]), bool(tpl[1])) == (False, True)
            conn.execute(
                text(
                    "INSERT INTO tiqora_itsm_change_freeze (id, name, enforcement, scope,"
                    " starts_at, ends_at, created_at, created_by, updated_at, updated_by)"
                    " VALUES (946101, 'chg94 freeze', 'block', 'all', :n, :n, :n, 1, :n, 1)"
                ),
                {"n": _NOW},
            )
            freeze = conn.execute(
                text(
                    "SELECT allow_emergency, valid FROM tiqora_itsm_change_freeze WHERE id = 946101"
                )
            ).one()
            assert (bool(freeze[0]), bool(freeze[1])) == (True, True)
            conn.execute(
                text(
                    "INSERT INTO tiqora_itsm_legacy_change (id, znuny_change_id, change_number,"
                    " znuny_created_at, znuny_changed_at, data, fingerprint, imported_at)"
                    " VALUES (946101, 946101, '2026-10-27-chg94', :n, :n, '{}', 'f', :n)"
                ),
                {"n": _NOW},
            )
            conn.execute(
                text(
                    "INSERT INTO tiqora_itsm_legacy_change_ref"
                    " (legacy_change_id, ref_kind, ref_key)"
                    " VALUES (946101, 'ticket', '946101')"
                )
            )
            legacy = conn.execute(
                text("SELECT workorder_count FROM tiqora_itsm_legacy_change WHERE id = 946101")
            ).scalar_one()
            ref = conn.execute(
                text(
                    "SELECT workorder_number FROM tiqora_itsm_legacy_change_ref"
                    " WHERE legacy_change_id = 946101"
                )
            ).scalar_one()
            assert (legacy, ref) == (0, 0)
    finally:
        _cleanup(engine)
        engine.dispose()


@pytest.mark.parametrize("alembic_env", _PARAMS, indirect=True)
def test_change_children_cascade(migrated: str) -> None:
    engine = create_engine(migrated)
    try:
        with engine.begin() as conn:
            _seed_tree(conn, 946110)
            _seed_tree(conn, 946111)
            for table, col in _CHILD_COUNTS:
                assert _count(conn, table, f"{col} = 946110") == 1, table
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM tiqora_itsm_change WHERE ticket_id = 946110"))
        with engine.begin() as conn:
            assert _count(conn, "tiqora_itsm_change", "ticket_id = 946110") == 0
            for table, col in _CHILD_COUNTS:
                assert _count(conn, table, f"{col} = 946110") == 0, table
                # the other change is untouched
                assert _count(conn, table, f"{col} = 946111") == 1, table
            # the meeting itself stays: only its agenda item went with the change
            assert _count(conn, "tiqora_itsm_cab_meeting", "id = 946110") == 1
    finally:
        _cleanup(engine)
        engine.dispose()


@pytest.mark.parametrize("alembic_env", _PARAMS, indirect=True)
def test_template_set_null_on_delete(migrated: str) -> None:
    engine = create_engine(migrated)
    try:
        with engine.begin() as conn:
            _seed_template(conn, 946120)
            _seed_cab(conn, 946120)
            _seed_change(conn, 946120, template_id=946120, cab_id=946120)
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM tiqora_itsm_change_template WHERE id = 946120"))
            conn.execute(text("DELETE FROM tiqora_itsm_cab WHERE id = 946120"))
        with engine.begin() as conn:
            row = conn.execute(
                text(
                    "SELECT template_id, cab_id, phase FROM tiqora_itsm_change"
                    " WHERE ticket_id = 946120"
                )
            ).one()
        # the change survives, only the links to the deleted template and CAB go
        assert tuple(row) == (None, None, "draft")
    finally:
        _cleanup(engine)
        engine.dispose()


@pytest.mark.parametrize("alembic_env", _PARAMS, indirect=True)
def test_cab_restrict_with_meetings(migrated: str) -> None:
    engine = create_engine(migrated)
    try:
        with engine.begin() as conn:
            _seed_cab(conn, 946130)
            conn.execute(
                text(
                    "INSERT INTO tiqora_itsm_cab_member (cab_id, member_kind, member_ref)"
                    " VALUES (946130, 'group', '12')"
                )
            )
            _seed_meeting(conn, 946130, 946130)
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.execute(text("DELETE FROM tiqora_itsm_cab WHERE id = 946130"))
        with engine.begin() as conn:
            assert _count(conn, "tiqora_itsm_cab", "id = 946130") == 1
            conn.execute(text("DELETE FROM tiqora_itsm_cab_meeting WHERE id = 946130"))
        # without meetings the CAB goes, and its members with it
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM tiqora_itsm_cab WHERE id = 946130"))
        with engine.begin() as conn:
            assert _count(conn, "tiqora_itsm_cab", "id = 946130") == 0
            assert _count(conn, "tiqora_itsm_cab_member", "cab_id = 946130") == 0
    finally:
        _cleanup(engine)
        engine.dispose()


@pytest.mark.parametrize("alembic_env", _PARAMS, indirect=True)
def test_cab_member_unique(migrated: str) -> None:
    engine = create_engine(migrated)
    sql = text(
        "INSERT INTO tiqora_itsm_cab_member (cab_id, member_kind, member_ref)"
        " VALUES (946131, :k, :r)"
    )
    try:
        with engine.begin() as conn:
            _seed_cab(conn, 946131)
            conn.execute(sql, {"k": "group", "r": "12"})
            # the same ref under another kind is a different member
            conn.execute(sql, {"k": "user", "r": "12"})
            conn.execute(sql, {"k": "customer_user", "r": "erika@example.org"})
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.execute(sql, {"k": "group", "r": "12"})
    finally:
        _cleanup(engine)
        engine.dispose()


@pytest.mark.parametrize("alembic_env", _PARAMS, indirect=True)
def test_task_template_key_unique(migrated: str) -> None:
    engine = create_engine(migrated)
    try:
        with engine.begin() as conn:
            _seed_change(conn, 946140)
            _seed_change(conn, 946141)
            conn.execute(_TASK_SQL, {"task": 946150, "chg": 946140, "key": "k1", "n": _NOW})
            # another key, and the same key under another change, are fine
            conn.execute(_TASK_SQL, {"task": 946151, "chg": 946140, "key": "k2", "n": _NOW})
            conn.execute(_TASK_SQL, {"task": 946152, "chg": 946141, "key": "k1", "n": _NOW})
            # tasks without a template key (added by hand) never collide
            conn.execute(_TASK_SQL, {"task": 946153, "chg": 946140, "key": None, "n": _NOW})
            conn.execute(_TASK_SQL, {"task": 946154, "chg": 946140, "key": None, "n": _NOW})
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.execute(_TASK_SQL, {"task": 946155, "chg": 946140, "key": "k1", "n": _NOW})
    finally:
        _cleanup(engine)
        engine.dispose()


@pytest.mark.parametrize("alembic_env", _PARAMS, indirect=True)
def test_downgrade_drops_change_tables(migrated: str) -> None:
    sync_url = migrated
    assert set(_TABLES) <= _tables(sync_url)
    _migrate(_PREVIOUS, down=True)
    present = _tables(sync_url)
    assert not set(_TABLES) & present
    # the approval tables of the previous revision stay
    assert {"tiqora_approval", "tiqora_itsm_link_type"} <= present
    assert _version(sync_url) == _PREVIOUS

    _migrate(_NEW)
    assert set(_TABLES) <= _tables(sync_url)
    engine = create_engine(sync_url)
    try:
        for name in _TABLES:
            _assert_table_matches_orm(engine, name)
    finally:
        engine.dispose()
