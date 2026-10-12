"""CMDB tables, seeds, add-on views and the migration literals (both dialects)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tests import _cmdb_helpers
from tests._cmdb_helpers import TICKET_OPEN, USER_ADMIN, CmdbWorld, make_class
from tiqora.cmdb.seed import (
    DEFAULT_CATALOG_ROWS,
    DEPLOYMENT_STATE,
    INCIDENT_STATE,
    YES_NO,
    seed_defaults,
)
from tiqora.cmdb.views import (
    VIEW_DDL,
    create_cmdb_views,
    is_privilege_error,
    view_statements,
    views_present,
)
from tiqora.db.tiqora.models import (
    TiqoraCmdbCatalog,
    TiqoraCmdbCi,
    TiqoraItsmLink,
)
from tiqora.itsm.link_type_defaults import DEFAULT_LINK_TYPES
from tiqora.itsm.models import TiqoraItsmLinkType
from tiqora.jobs.service import utc_now

pytestmark = pytest.mark.db

# fixtures of the shared world (module attributes are what pytest collects)
cmdb_db = _cmdb_helpers.cmdb_db
_cmdb_rows_reset = _cmdb_helpers._cmdb_rows_reset

_MIGRATION = Path(__file__).parent.parent / "alembic" / "versions_tiqora" / "20261010_0079_cmdb.py"


def _load_migration_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_mig_0079_cmdb", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _catalog_id(session: AsyncSession, catalog_class: str, key: str) -> int:
    return int(
        (
            await session.execute(
                select(TiqoraCmdbCatalog.id).where(
                    TiqoraCmdbCatalog.catalog_class == catalog_class,
                    TiqoraCmdbCatalog.item_key == key,
                )
            )
        ).scalar_one()
    )


async def _add_ci(session: AsyncSession, class_id: int, name: str, *, incident: str) -> int:
    now = utc_now()
    dep = await _catalog_id(session, DEPLOYMENT_STATE, "production")
    inc = await _catalog_id(session, INCIDENT_STATE, incident)
    ci = TiqoraCmdbCi(
        number=f"CMDB4{name}",
        class_id=class_id,
        name=name,
        deployment_state_id=dep,
        incident_state_id=inc,
        cur_incident_state_id=inc,
        created_by=USER_ADMIN,
        created_at=now,
        changed_by=USER_ADMIN,
        changed_at=now,
    )
    session.add(ci)
    await session.flush()
    return int(ci.id)


def _link(src: tuple[str, str], dst: tuple[str, str], link_type: str) -> TiqoraItsmLink:
    return TiqoraItsmLink(
        source_kind=src[0],
        source_key=src[1],
        target_kind=dst[0],
        target_key=dst[1],
        link_type=link_type,
        created_by=USER_ADMIN,
        created_at=utc_now(),
    )


async def test_tables_and_seed(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        keys = set(
            (
                await s.execute(select(TiqoraCmdbCatalog.catalog_class, TiqoraCmdbCatalog.item_key))
            ).all()
        )
        assert (DEPLOYMENT_STATE, "production") in keys
        assert (INCIDENT_STATE, "incident") in keys
        assert (YES_NO, "yes") in keys
        assert {(r.catalog_class, r.item_key) for r in DEFAULT_CATALOG_ROWS} <= keys
        fn = (
            await s.execute(
                select(TiqoraCmdbCatalog.functionality).where(
                    TiqoraCmdbCatalog.catalog_class == INCIDENT_STATE,
                    TiqoraCmdbCatalog.item_key == "warning",
                )
            )
        ).scalar_one()
        assert fn == "warning"
        dep = (
            await s.execute(
                select(TiqoraItsmLinkType).where(TiqoraItsmLinkType.name == "DependsOn")
            )
        ).scalar_one()
        assert dep.propagation == "source" and dep.pointed
        names = set((await s.execute(select(TiqoraItsmLinkType.name))).scalars())
        assert {d.name for d in DEFAULT_LINK_TYPES} <= names


async def test_seed_is_idempotent(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        before = (await s.execute(select(func.count()).select_from(TiqoraCmdbCatalog))).scalar_one()
        types_before = (
            await s.execute(select(func.count()).select_from(TiqoraItsmLinkType))
        ).scalar_one()
        await seed_defaults(s)
        await s.commit()
        after = (await s.execute(select(func.count()).select_from(TiqoraCmdbCatalog))).scalar_one()
        types_after = (
            await s.execute(select(func.count()).select_from(TiqoraItsmLinkType))
        ).scalar_one()
        assert before == len(DEFAULT_CATALOG_ROWS)
        assert after == before
        assert types_after == types_before


async def test_seed_restores_a_deleted_row(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        row = (
            await s.execute(
                select(TiqoraCmdbCatalog).where(
                    TiqoraCmdbCatalog.catalog_class == YES_NO, TiqoraCmdbCatalog.item_key == "no"
                )
            )
        ).scalar_one()
        await s.delete(row)
        await s.commit()
        await seed_defaults(s)
        await s.commit()
        again = (
            await s.execute(
                select(TiqoraCmdbCatalog.name).where(
                    TiqoraCmdbCatalog.catalog_class == YES_NO, TiqoraCmdbCatalog.item_key == "no"
                )
            )
        ).scalar_one()
        assert again == "No"


async def test_views_select_on_both_dialects(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cls_id = await make_class(s, portal_visible=True)
        ci_id = await _add_ci(s, cls_id, "a", incident="incident")
        other_id = await _add_ci(s, cls_id, "b", incident="operational")
        s.add_all(
            [
                _link(("ci", str(ci_id)), ("ticket", str(TICKET_OPEN)), "RelevantTo"),
                _link(("service", "7"), ("ci", str(ci_id)), "DependsOn"),
                _link(("ci", str(ci_id)), ("ci", str(other_id)), "DependsOn"),
            ]
        )
        await s.commit()

        links = [
            tuple(r)
            for r in (
                await s.execute(
                    text(
                        "SELECT ci_id, object_kind, object_key, link_type, direction"
                        " FROM tiqora_v1_itsm_ci_links"
                    )
                )
            ).all()
        ]
        assert (ci_id, "ticket", str(TICKET_OPEN), "RelevantTo", "source") in links
        assert (ci_id, "service", "7", "DependsOn", "target") in links
        # CI to CI: one row per end, each seen from its own CI
        assert (ci_id, "ci", str(other_id), "DependsOn", "source") in links
        assert (other_id, "ci", str(ci_id), "DependsOn", "target") in links
        assert len(links) == 4

        row = (
            await s.execute(
                text(
                    "SELECT ci_id, number, name, class_id, class_key, class_name, class_group_id,"
                    " deployment_state, deployment_functionality, incident_state,"
                    " incident_functionality, changed, class_portal_visible"
                    " FROM tiqora_v1_itsm_ci WHERE ci_id = :i"
                ),
                {"i": ci_id},
            )
        ).one()
        assert row[0] == ci_id and row[1] == "CMDB4a" and row[2] == "a"
        assert row[3] == cls_id and row[4] == "cmdb4srv"
        assert row[7:11] == ("Production", "productive", "Incident", "incident")
        assert bool(row[12]) is True
        assert row[11] is not None
        assert await views_present(s)


async def test_view_reports_the_derived_incident_state(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cls_id = await make_class(s)
        ci_id = await _add_ci(s, cls_id, "c", incident="operational")
        warning = await _catalog_id(s, INCIDENT_STATE, "warning")
        ci = (await s.execute(select(TiqoraCmdbCi).where(TiqoraCmdbCi.id == ci_id))).scalar_one()
        ci.cur_incident_state_id = warning
        await s.commit()
        state = (
            await s.execute(
                text("SELECT incident_state FROM tiqora_v1_itsm_ci WHERE ci_id = :i"), {"i": ci_id}
            )
        ).scalar_one()
        assert state == "Warning"


async def test_link_type_must_exist_and_pair_is_unique(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        s.add(_link(("ci", "1"), ("ticket", "2"), "NoSuchType"))
        with pytest.raises(IntegrityError):
            await s.commit()
        await s.rollback()
        s.add(_link(("ci", "1"), ("ticket", "2"), "RelevantTo"))
        await s.commit()
        s.add(_link(("ci", "1"), ("ticket", "2"), "RelevantTo"))
        with pytest.raises(IntegrityError):
            await s.commit()
        await s.rollback()
        # a type that still has links cannot be deleted (RESTRICT)
        await s.execute(text("DELETE FROM tiqora_itsm_link WHERE link_type = 'ConnectedTo'"))
        with pytest.raises(IntegrityError):
            await s.execute(text("DELETE FROM tiqora_itsm_link_type WHERE name = 'RelevantTo'"))
        await s.rollback()


def test_create_views_is_idempotent(cmdb_db: CmdbWorld) -> None:
    engine = create_engine(cmdb_db.sync_url)
    try:
        with engine.begin() as conn:
            assert create_cmdb_views(conn) is True
            assert create_cmdb_views(conn) is True
            assert conn.execute(text("SELECT COUNT(*) FROM tiqora_v1_itsm_ci")).scalar_one() == 0
    finally:
        engine.dispose()


def test_create_views_without_privilege_returns_false_on_postgres(cmdb_db: CmdbWorld) -> None:
    """A role that may not create objects: no exception, ``False`` (PG ``42501``)."""
    if cmdb_db.dialect != "postgresql":
        pytest.skip("privilege path of CREATE VIEW is tested on PostgreSQL")
    admin = create_engine(cmdb_db.sync_url, isolation_level="AUTOCOMMIT")
    role = "cmdb4_noview"
    public_create = False
    try:
        with admin.connect() as conn:
            conn.execute(text(f"DROP ROLE IF EXISTS {role}"))
            conn.execute(text(f"CREATE ROLE {role} LOGIN PASSWORD 'cmdb4pw'"))
            public_create = bool(
                conn.execute(
                    text("SELECT has_schema_privilege(:r, 'public', 'CREATE')"), {"r": role}
                ).scalar_one()
            )
            if public_create:  # PostgreSQL < 15 grants CREATE on public to everyone
                conn.execute(text("REVOKE CREATE ON SCHEMA public FROM PUBLIC"))
        url = create_engine(cmdb_db.sync_url).url.set(username=role, password="cmdb4pw")
        limited = create_engine(url)
        try:
            with limited.begin() as conn:
                assert create_cmdb_views(conn) is False
                # the failed statement did not poison the transaction (savepoint)
                assert conn.execute(text("SELECT 1")).scalar_one() == 1
        finally:
            limited.dispose()
    finally:
        with admin.connect() as conn:
            if public_create:
                conn.execute(text("GRANT CREATE ON SCHEMA public TO PUBLIC"))
            conn.execute(text(f"DROP ROLE IF EXISTS {role}"))
        admin.dispose()


def test_is_privilege_error_recognises_both_servers() -> None:
    class _Pg(Exception):
        sqlstate = "42501"

    class _Wrapper(Exception):
        def __init__(self, orig: BaseException) -> None:
            super().__init__("wrapped")
            self.orig = orig

    assert is_privilege_error(_Wrapper(_Pg("denied")))
    assert is_privilege_error(_Wrapper(Exception(1142, "CREATE VIEW command denied")))
    assert not is_privilege_error(_Wrapper(Exception(1050, "Table already exists")))
    assert not is_privilege_error(_Wrapper(_Pg.__bases__[0]("other")))


def test_view_statements_pick_the_dialect_cast() -> None:
    assert "AS SIGNED" in view_statements("mysql")["tiqora_v1_itsm_ci_links"]
    assert "AS SIGNED" in view_statements("mariadb")["tiqora_v1_itsm_ci_links"]
    assert "AS INTEGER" in view_statements("postgresql")["tiqora_v1_itsm_ci_links"]
    assert "{INT}" not in view_statements("postgresql")["tiqora_v1_itsm_ci_links"]


def test_migration_view_literals_match_module() -> None:
    mod = _load_migration_module()
    assert mod._VIEWS == VIEW_DDL


def test_migration_seed_literals_match_module() -> None:
    mod = _load_migration_module()
    assert [tuple(r) for r in mod._CATALOG_ROWS] == [tuple(r) for r in DEFAULT_CATALOG_ROWS]


def test_migration_chain_position() -> None:
    mod = _load_migration_module()
    assert mod.revision == "20261010_0079"
    assert mod.down_revision == "20261010_0077"
