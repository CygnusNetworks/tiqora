"""Approval tables (migration ``20261010_0083``) on MariaDB and PostgreSQL.

The migration builds the tables; the tests compare them with the ORM metadata, exercise the
constraints that matter (one vote per approver, one approver per step and principal, unique
token hash, cascades, SET NULL on the template) and run a downgrade/upgrade round trip. Sync
tests on purpose: Alembic's env.py runs its own event loop. Seed ids stay in 970000-970099.
Leaves the schema at head.
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
from tiqora.approvals.models import APPROVAL_TICKET_TABLES
from tiqora.config import get_settings
from tiqora.db.tiqora.base import TiqoraBase

pytestmark = pytest.mark.db

_PREVIOUS = "20261010_0082"
_NEW = "20261010_0083"
_NOW = datetime(2026, 10, 15, 8, 0, 0)
_TABLES = (
    "tiqora_approval",
    "tiqora_approval_step",
    "tiqora_approval_approver",
    "tiqora_approval_vote",
    "tiqora_approval_token",
    "tiqora_approval_event",
    "tiqora_approval_template",
    "tiqora_approval_list",
    "tiqora_approval_list_member",
    "tiqora_approval_delegation",
)
_PARAMS = ["mariadb_znuny_url", "postgres_znuny_url"]
_TICKET = 970001


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
    """A schema migrated from scratch to 0083 (so every table is migration-built)."""
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
        conn.execute(text("DELETE FROM tiqora_approval WHERE id BETWEEN 970000 AND 970099"))
        conn.execute(
            text("DELETE FROM tiqora_approval_template WHERE id BETWEEN 970000 AND 970099")
        )
        conn.execute(text("DELETE FROM tiqora_approval_list WHERE id BETWEEN 970000 AND 970099"))


def _seed_approval(
    conn: object,
    approval_id: int,
    *,
    requester: str = "agent:970001",
    template_id: int | None = None,
) -> None:
    conn.execute(  # type: ignore[attr-defined]
        text(
            "INSERT INTO tiqora_approval (id, ticket_id, subject_type, approval_key, title,"
            " template_id, status, requester, created_by, created_at)"
            " VALUES (:id, :t, 'ticket', 'default', 'appr97 request', :tpl, 'pending', :r, 1, :n)"
        ),
        {"id": approval_id, "t": _TICKET, "tpl": template_id, "r": requester, "n": _NOW},
    )


def _seed_step(conn: object, step_id: int, approval_id: int, position: int = 1) -> None:
    conn.execute(  # type: ignore[attr-defined]
        text(
            "INSERT INTO tiqora_approval_step (id, approval_id, ticket_id, position, stage, name,"
            " rule, status, spec) VALUES (:id, :a, :t, :p, 1, 'Step', 'any', 'active', '{}')"
        ),
        {"id": step_id, "a": approval_id, "t": _TICKET, "p": position},
    )


def _seed_approver(
    conn: object,
    approver_id: int,
    approval_id: int,
    step_id: int,
    principal: str = "agent:970002",
    customer_login: str | None = None,
) -> None:
    conn.execute(  # type: ignore[attr-defined]
        text(
            "INSERT INTO tiqora_approval_approver (id, approval_id, step_id, ticket_id, principal,"
            " customer_login, source_kind, status, added_at)"
            " VALUES (:id, :a, :s, :t, :p, :c, 'user', 'pending', :n)"
        ),
        {
            "id": approver_id,
            "a": approval_id,
            "s": step_id,
            "t": _TICKET,
            "p": principal,
            "c": customer_login,
            "n": _NOW,
        },
    )


_VOTE_SQL = text(
    "INSERT INTO tiqora_approval_vote (approval_id, step_id, approver_id, ticket_id, decision,"
    " decided_by, channel, decided_at)"
    " VALUES (:a, :s, :ap, :t, :d, 'agent:970002', 'ui', :n)"
)


def _seed_tree(conn: object, approval_id: int) -> None:
    """One request with one step, one approver, a vote, a token and an event."""
    step_id = approval_id + 100_000
    approver_id = approval_id + 200_000
    _seed_approval(conn, approval_id)
    _seed_step(conn, step_id, approval_id)
    _seed_approver(conn, approver_id, approval_id, step_id)
    conn.execute(  # type: ignore[attr-defined]
        _VOTE_SQL,
        {
            "a": approval_id,
            "s": step_id,
            "ap": approver_id,
            "t": _TICKET,
            "d": "approved",
            "n": _NOW,
        },
    )
    conn.execute(  # type: ignore[attr-defined]
        text(
            "INSERT INTO tiqora_approval_token (approval_id, approver_id, ticket_id, token_hash,"
            " created_at, expires_at) VALUES (:a, :ap, :t, :h, :n, :n)"
        ),
        {"a": approval_id, "ap": approver_id, "t": _TICKET, "h": f"{approval_id:064d}", "n": _NOW},
    )
    conn.execute(  # type: ignore[attr-defined]
        text(
            "INSERT INTO tiqora_approval_event (approval_id, ticket_id, at, action, actor)"
            " VALUES (:a, :t, :n, 'requested', 'system')"
        ),
        {"a": approval_id, "t": _TICKET, "n": _NOW},
    )


def _count(conn: object, table: str, where: str) -> int:
    return int(
        conn.execute(text(f"SELECT COUNT(*) FROM {table} WHERE {where}")).scalar_one()  # type: ignore[attr-defined]
    )


@pytest.mark.parametrize("alembic_env", _PARAMS, indirect=True)
def test_migration_creates_approval_tables(migrated: str) -> None:
    sync_url = migrated
    assert _version(sync_url) == _NEW
    assert set(_TABLES) <= _tables(sync_url)
    assert len(_TABLES) == 10
    engine = create_engine(sync_url)
    try:
        for name in _TABLES:
            _assert_table_matches_orm(engine, name)
        insp = inspect(engine)
        indexes = {
            "tiqora_approval": {
                "ix_tiqora_approval_ticket_key": ["ticket_id", "approval_key", "id"],
                "ix_tiqora_approval_status": ["status", "id"],
            },
            "tiqora_approval_step": {
                "ix_tiqora_approval_step_due": ["status", "due_at"],
                "ix_tiqora_approval_step_ticket": ["ticket_id"],
            },
            "tiqora_approval_approver": {
                "ix_tiqora_approval_approver_inbox": ["principal", "status"],
                "ix_tiqora_approval_approver_user": ["user_id", "status"],
                "ix_tiqora_approval_approver_ticket": ["ticket_id"],
            },
            "tiqora_approval_vote": {"ix_tiqora_approval_vote_ticket": ["ticket_id"]},
            "tiqora_approval_token": {
                "ix_tiqora_approval_token_approver": ["approver_id"],
                "ix_tiqora_approval_token_ticket": ["ticket_id"],
            },
            "tiqora_approval_event": {
                "ix_tiqora_approval_event_approval": ["approval_id", "id"],
                "ix_tiqora_approval_event_ticket": ["ticket_id"],
            },
            "tiqora_approval_delegation": {
                "ix_tiqora_approval_delegation_sub": ["substitute_user_id"],
                "ix_tiqora_approval_delegation_user": ["user_id"],
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
            "tiqora_approval_step": ("uq_tiqora_approval_step_pos", ["approval_id", "position"]),
            "tiqora_approval_approver": ("uq_tiqora_approval_approver", ["step_id", "principal"]),
            "tiqora_approval_vote": ("uq_tiqora_approval_vote_approver", ["approver_id"]),
            "tiqora_approval_token": ("uq_tiqora_approval_token_hash", ["token_hash"]),
            "tiqora_approval_template": ("uq_tiqora_approval_template_key", ["template_key"]),
            "tiqora_approval_list": ("uq_tiqora_approval_list_name", ["name"]),
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
        # a raw insert that leaves every defaulted column out: the DDL defaults apply
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO tiqora_approval_template (id, template_key, name, definition,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (970001, 'appr97-tpl', 'T', '{}', :n, 1, :n, 1)"
                ),
                {"n": _NOW},
            )
            conn.execute(
                text(
                    "INSERT INTO tiqora_approval_list (id, name, create_time, create_by,"
                    " change_time, change_by) VALUES (970001, 'appr97-list', :n, 1, :n, 1)"
                ),
                {"n": _NOW},
            )
            _seed_approval(conn, 970001)
            _seed_step(conn, 970101, 970001)
            _seed_approver(conn, 970201, 970001, 970101)
            tpl = conn.execute(
                text(
                    "SELECT subject_types, origin, valid FROM tiqora_approval_template"
                    " WHERE id = 970001"
                )
            ).one()
            assert (tpl[0], tpl[1], bool(tpl[2])) == ("[]", "admin", True)
            assert bool(
                conn.execute(
                    text("SELECT valid FROM tiqora_approval_list WHERE id = 970001")
                ).scalar_one()
            )
            assert not bool(
                conn.execute(
                    text("SELECT allow_self_approval FROM tiqora_approval WHERE id = 970001")
                ).scalar_one()
            )
            step = conn.execute(
                text("SELECT reminder_count FROM tiqora_approval_step WHERE id = 970101")
            ).scalar_one()
            assert step == 0
            assert not bool(
                conn.execute(
                    text("SELECT decisive FROM tiqora_approval_approver WHERE id = 970201")
                ).scalar_one()
            )
    finally:
        _cleanup(engine)
        engine.dispose()


@pytest.mark.parametrize("alembic_env", _PARAMS, indirect=True)
def test_vote_unique_per_approver(migrated: str) -> None:
    engine = create_engine(migrated)
    params = {"a": 970010, "s": 970110, "ap": 970210, "t": _TICKET, "d": "approved", "n": _NOW}
    try:
        with engine.begin() as conn:
            _seed_approval(conn, 970010)
            _seed_step(conn, 970110, 970010)
            _seed_approver(conn, 970210, 970010, 970110)
            _seed_approver(conn, 970211, 970010, 970110, "agent:970003")
            conn.execute(_VOTE_SQL, params)
            # a different approver of the same step votes independently
            conn.execute(_VOTE_SQL, {**params, "ap": 970211})
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.execute(_VOTE_SQL, {**params, "d": "rejected"})
    finally:
        _cleanup(engine)
        engine.dispose()


@pytest.mark.parametrize("alembic_env", _PARAMS, indirect=True)
def test_approver_unique_per_step_principal(migrated: str) -> None:
    engine = create_engine(migrated)
    try:
        with engine.begin() as conn:
            _seed_approval(conn, 970011)
            _seed_step(conn, 970111, 970011, 1)
            _seed_step(conn, 970112, 970011, 2)
            _seed_approver(conn, 970211, 970011, 970111, "agent:970002")
            # the same principal in another step and another principal in this step are fine
            _seed_approver(conn, 970212, 970011, 970112, "agent:970002")
            _seed_approver(conn, 970213, 970011, 970111, "agent:970003")
        with pytest.raises(IntegrityError), engine.begin() as conn:
            _seed_approver(conn, 970214, 970011, 970111, "agent:970002")
    finally:
        _cleanup(engine)
        engine.dispose()


@pytest.mark.parametrize("alembic_env", _PARAMS, indirect=True)
def test_token_hash_unique(migrated: str) -> None:
    engine = create_engine(migrated)
    sql = text(
        "INSERT INTO tiqora_approval_token (approval_id, approver_id, ticket_id, token_hash,"
        " created_at, expires_at) VALUES (970012, 970212, :t, :h, :n, :n)"
    )
    try:
        with engine.begin() as conn:
            _seed_approval(conn, 970012)
            _seed_step(conn, 970112, 970012)
            _seed_approver(conn, 970212, 970012, 970112)
            conn.execute(sql, {"t": _TICKET, "h": "a" * 64, "n": _NOW})
            conn.execute(sql, {"t": _TICKET, "h": "b" * 64, "n": _NOW})
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.execute(sql, {"t": _TICKET, "h": "a" * 64, "n": _NOW})
    finally:
        _cleanup(engine)
        engine.dispose()


@pytest.mark.parametrize("alembic_env", _PARAMS, indirect=True)
def test_cascade_from_approval(migrated: str) -> None:
    engine = create_engine(migrated)
    try:
        with engine.begin() as conn:
            _seed_tree(conn, 970010)
            _seed_tree(conn, 970011)
            for table in (
                "tiqora_approval_step",
                "tiqora_approval_approver",
                "tiqora_approval_vote",
                "tiqora_approval_token",
                "tiqora_approval_event",
            ):
                assert _count(conn, table, "approval_id = 970010") == 1, table
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM tiqora_approval WHERE id = 970010"))
        with engine.begin() as conn:
            assert _count(conn, "tiqora_approval", "id = 970010") == 0
            for table in (
                "tiqora_approval_step",
                "tiqora_approval_approver",
                "tiqora_approval_vote",
                "tiqora_approval_token",
                "tiqora_approval_event",
            ):
                assert _count(conn, table, "approval_id = 970010") == 0, table
                # the other request is untouched
                assert _count(conn, table, "approval_id = 970011") == 1, table
    finally:
        _cleanup(engine)
        engine.dispose()


@pytest.mark.parametrize("alembic_env", _PARAMS, indirect=True)
def test_template_delete_sets_null(migrated: str) -> None:
    engine = create_engine(migrated)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO tiqora_approval_template (id, template_key, name, definition,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (970020, 'appr97-del', 'T', '{}', :n, 1, :n, 1)"
                ),
                {"n": _NOW},
            )
            _seed_approval(conn, 970020, template_id=970020)
            assert (
                conn.execute(
                    text("SELECT template_id FROM tiqora_approval WHERE id = 970020")
                ).scalar_one()
                == 970020
            )
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM tiqora_approval_template WHERE id = 970020"))
        with engine.begin() as conn:
            row = conn.execute(
                text("SELECT template_id, status FROM tiqora_approval WHERE id = 970020")
            ).one()
        # the request survives, only the link to the deleted template goes
        assert tuple(row) == (None, "pending")
    finally:
        _cleanup(engine)
        engine.dispose()


@pytest.mark.parametrize("alembic_env", _PARAMS, indirect=True)
def test_list_member_cascade(migrated: str) -> None:
    engine = create_engine(migrated)
    sql = text("INSERT INTO tiqora_approval_list_member (list_id, user_id) VALUES (:l, :u)")
    try:
        with engine.begin() as conn:
            for list_id in (970030, 970031):
                conn.execute(
                    text(
                        "INSERT INTO tiqora_approval_list (id, name, create_time, create_by,"
                        " change_time, change_by) VALUES (:i, :n, :t, 1, :t, 1)"
                    ),
                    {"i": list_id, "n": f"appr97-list-{list_id}", "t": _NOW},
                )
                conn.execute(sql, {"l": list_id, "u": 970001})
                conn.execute(sql, {"l": list_id, "u": 970002})
        # composite primary key: a member twice is refused, a missing list too
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.execute(sql, {"l": 970030, "u": 970001})
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.execute(sql, {"l": 970039, "u": 970001})
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM tiqora_approval_list WHERE id = 970030"))
        with engine.begin() as conn:
            assert _count(conn, "tiqora_approval_list_member", "list_id = 970030") == 0
            assert _count(conn, "tiqora_approval_list_member", "list_id = 970031") == 2
    finally:
        _cleanup(engine)
        engine.dispose()


@pytest.mark.parametrize("alembic_env", _PARAMS, indirect=True)
def test_long_customer_login_round_trip(migrated: str) -> None:
    """Znuny's customer login is VARCHAR(200): ``customer:`` + 200 characters fits everywhere."""
    login = "appr97-" + "x" * 193
    assert len(login) == 200
    principal = f"customer:{login}"
    assert len(principal) == 209
    engine = create_engine(migrated)
    try:
        with engine.begin() as conn:
            _seed_approval(conn, 970040, requester=principal)
            _seed_step(conn, 970140, 970040)
            _seed_approver(conn, 970240, 970040, 970140, principal, login)
            conn.execute(
                text(
                    "INSERT INTO tiqora_approval_vote (approval_id, step_id, approver_id,"
                    " ticket_id, decision, decided_by, channel, decided_at)"
                    " VALUES (970040, 970140, 970240, :t, 'approved', :p, 'portal', :n)"
                ),
                {"t": _TICKET, "p": principal, "n": _NOW},
            )
        with engine.begin() as conn:
            row = conn.execute(
                text(
                    "SELECT principal, customer_login FROM tiqora_approval_approver"
                    " WHERE id = 970240"
                )
            ).one()
            assert tuple(row) == (principal, login)
            assert (
                conn.execute(
                    text("SELECT requester FROM tiqora_approval WHERE id = 970040")
                ).scalar_one()
                == principal
            )
            assert (
                conn.execute(
                    text("SELECT decided_by FROM tiqora_approval_vote WHERE approver_id = 970240")
                ).scalar_one()
                == principal
            )
            # the lookup by principal (inbox index) finds exactly this row
            assert _count(conn, "tiqora_approval_approver", f"principal = '{principal}'") == 1
        # the (step_id, principal) unique key covers the full width
        with pytest.raises(IntegrityError), engine.begin() as conn:
            _seed_approver(conn, 970241, 970040, 970140, principal, login)
    finally:
        _cleanup(engine)
        engine.dispose()


@pytest.mark.parametrize("alembic_env", _PARAMS, indirect=True)
def test_downgrade_drops_approval_tables(migrated: str) -> None:
    sync_url = migrated
    assert set(_TABLES) <= _tables(sync_url)
    _migrate(_PREVIOUS, down=True)
    present = _tables(sync_url)
    assert not set(_TABLES) & present
    assert not {t for t in present if t.startswith("tiqora_approval")}
    assert "tiqora_plugin" in present
    assert _version(sync_url) == _PREVIOUS

    _migrate(_NEW)
    assert set(_TABLES) <= _tables(sync_url)
    engine = create_engine(sync_url)
    try:
        for name in _TABLES:
            _assert_table_matches_orm(engine, name)
    finally:
        engine.dispose()


def test_ticket_tables_order() -> None:
    assert APPROVAL_TICKET_TABLES == (
        "tiqora_approval_token",
        "tiqora_approval_vote",
        "tiqora_approval_approver",
        "tiqora_approval_event",
        "tiqora_approval_step",
        "tiqora_approval",
    )
    # every approval table except templates/lists/delegations, each keyed by ticket_id
    keyed = {
        name
        for name in _TABLES
        if name not in {"tiqora_approval_template", "tiqora_approval_list"}
        and "ticket_id" in TiqoraBase.metadata.tables[name].columns
    }
    assert keyed == set(APPROVAL_TICKET_TABLES)
    position = {name: i for i, name in enumerate(APPROVAL_TICKET_TABLES)}
    assert position["tiqora_approval"] == len(APPROVAL_TICKET_TABLES) - 1
    # children before parents along every foreign key inside the set
    for name in APPROVAL_TICKET_TABLES:
        for fk in TiqoraBase.metadata.tables[name].foreign_keys:
            parent = fk.column.table.name
            if parent in position:
                assert position[name] < position[parent], (name, parent)
