"""Shared world for the CMDB DB/API tests (band 740000-749999, names ``cmdb4``).

Users: ``cmdb4a`` (A, ``rw`` on GROUP_RW, ``ro`` on GROUP_RO), ``cmdb4b`` (B, ``ro`` on
GROUP_RO only), ``cmdb4admin`` (ADMIN, ``rw`` on ``admin``). Queues: QUEUE_RW (GROUP_RW),
QUEUE_HIDDEN (GROUP_HIDDEN). Customers ``cmdb4-cust-a@example.org`` (company ``cmdb4-co-a``,
extra mapping to ``cmdb4-co-c``) and ``cmdb4-cust-b@example.org`` (company ``cmdb4-co-b``).
Services 740400 and 740401 (``cmdb4-service``, ``cmdb4-service-2``). Tickets 740300 (QUEUE_RW,
customer a, open), 740301 (QUEUE_HIDDEN, open), 740302 (QUEUE_RW, closed).

Test modules re-export ``cmdb_db`` and ``_cmdb_rows_reset`` (the latter autouse) as module
attributes, which is what pytest collects (a plain import would trip ruff's F811):

    from tests import _cmdb_helpers
    cmdb_db = _cmdb_helpers.cmdb_db
    _cmdb_rows_reset = _cmdb_helpers._cmdb_rows_reset

``cmdb_db`` is module scoped: it creates the ``tiqora_*`` tables, the two add-on views and the
seeds once per module and dialect; ``_cmdb_rows_reset`` empties every non-seed CMDB row between
tests so ``make_class(key="cmdb4srv")`` works in every test.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any

import pytest
from fastapi import Depends
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db, get_session_factory_dep
from tiqora.cmdb.classes import ClassData, ClassService
from tiqora.cmdb.definition import AttributeDef, parse_attrs
from tiqora.cmdb.seed import (
    DEFAULT_CATALOG_ROWS,
    DEPLOYMENT_STATE,
    INCIDENT_STATE,
    seed_defaults,
)
from tiqora.cmdb.views import create_cmdb_views, drop_cmdb_views
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraCmdbCatalog
from tiqora.domain.auth import AuthenticatedUser
from tiqora.extensions import get_registry
from tiqora.itsm.link_type_defaults import DEFAULT_LINK_TYPES
from tiqora.jobs.runner import JobRunner

__all__ = [
    "ATTRS",
    "CUSTOMER_A",
    "CUSTOMER_B",
    "GROUP_HIDDEN",
    "GROUP_RO",
    "GROUP_RW",
    "QUEUE_HIDDEN",
    "QUEUE_RW",
    "SERVER_ATTRS",
    "SERVICE_ID",
    "SERVICE_ID_2",
    "SERVER_ATTRS_WITH_MANUAL",
    "TICKET_CLOSED",
    "TICKET_HIDDEN",
    "TICKET_OPEN",
    "USER_A",
    "USER_ADMIN",
    "USER_B",
    "CmdbWorld",
    "_cmdb_rows_reset",
    "_dep",
    "_inc",
    "cleanup_world",
    "client_for",
    "cmdb_db",
    "make_ci",
    "make_class",
    "make_link_type",
    "portal_client_for",
    "parse_attrs",
    "run_jobs_until_idle",
    "set_mode",
    "to_async_url",
]

GROUP_RW, GROUP_RO, GROUP_HIDDEN = 740010, 740011, 740012
USER_A, USER_B, USER_ADMIN = 740100, 740101, 740102
QUEUE_RW, QUEUE_HIDDEN = 740200, 740201
TICKET_OPEN, TICKET_HIDDEN, TICKET_CLOSED = 740300, 740301, 740302
SERVICE_ID, SERVICE_ID_2 = 740400, 740401
CUSTOMER_A, CUSTOMER_B = "cmdb4-cust-a@example.org", "cmdb4-cust-b@example.org"

_USERS = {
    USER_A: ("cmdb4a", "Anna", "Alpha"),
    USER_B: ("cmdb4b", "Boris", "Beta"),
    USER_ADMIN: ("cmdb4admin", "Ada", "Admin"),
}
_T = datetime(2026, 10, 1, 8, 0)
_P = {"lo": 740000, "hi": 749999}


def _attr(
    key: str,
    name: str,
    type_: str,
    *,
    searchable: bool = False,
    portal: bool = False,
    count_max: int = 1,
    options: dict[str, Any] | None = None,
    sub: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "key": key,
        "name": name,
        "type": type_,
        "required": False,
        "searchable": searchable,
        "portal": portal,
        "count_min": 0,
        "count_max": count_max,
        "count_default": 1,
        "default": None,
        "help": None,
        "options": options or {},
        "sub": sub or [],
    }


#: attribute definitions of the test class ``cmdb4srv`` (shape of ``AttributeDef``, D4)
SERVER_ATTRS: list[dict[str, Any]] = [
    _attr("Vendor", "Vendor", "text", searchable=True),
    _attr("Model", "Model", "text"),
    _attr("SerialNumber", "Serial number", "text", searchable=True),
    _attr("CPU", "CPU cores", "integer"),
    _attr("RAM", "RAM (GB)", "decimal"),
    _attr(
        "NIC",
        "Network interface",
        "text",
        count_max=3,
        sub=[_attr("IPAddress", "IP address", "ip", searchable=True, count_max=4)],
    ),
    _attr("Owner", "Owner", "customer_user", portal=True),
    _attr("Location", "Location", "ci_ref"),
    _attr("Purchase", "Purchase date", "date"),
    _attr("Cost", "Cost", "money", options={"currency": "EUR"}),
    _attr("Notes", "Notes", "textarea"),
    _attr(
        "Specs",
        "Specifications",
        "group",
        sub=[_attr("Disk", "Disk", "text", count_max=4)],
    ),
]


#: ``SERVER_ATTRS`` plus an ``attachment`` attribute ``Manual`` (CI attachment tests)
SERVER_ATTRS_WITH_MANUAL: list[dict[str, Any]] = [
    *SERVER_ATTRS,
    _attr("Manual", "Manual", "attachment"),
]


#: ``SERVER_ATTRS`` parsed into the typed attribute tree
ATTRS: list[AttributeDef] = parse_attrs(SERVER_ATTRS)


def to_async_url(url: str) -> str:
    return (
        url.replace("postgresql+psycopg2://", "postgresql+asyncpg://")
        .replace("mysql+pymysql://", "mysql+aiomysql://")
        .replace("postgresql://", "postgresql+asyncpg://")
    )


@dataclass
class CmdbWorld:
    factory: async_sessionmaker[AsyncSession]
    sync_url: str
    dialect: str  #: ``mysql`` | ``postgresql``

    def sql(self, sql: str, **params: Any) -> list[Any]:
        engine = create_engine(self.sync_url)
        try:
            with engine.begin() as conn:
                res = conn.execute(text(sql), params)
                return list(res.all()) if res.returns_rows else []
        finally:
            engine.dispose()


# --- world rows -----------------------------------------------------------------------

_ZNUNY_CLEANUP = (
    "DELETE FROM ticket WHERE id BETWEEN 740300 AND 740399",
    "DELETE FROM service WHERE id BETWEEN 740400 AND 740499",
    "DELETE FROM customer_user_customer WHERE user_id LIKE 'cmdb4-cust-%'",
    "DELETE FROM customer_user WHERE login LIKE 'cmdb4-cust-%'",
    "DELETE FROM customer_company WHERE customer_id LIKE 'cmdb4-co-%'",
    "DELETE FROM group_user WHERE user_id BETWEEN 740100 AND 740199"
    " OR group_id BETWEEN 740010 AND 740019",
    "DELETE FROM queue WHERE id BETWEEN 740200 AND 740299",
    "DELETE FROM permission_groups WHERE id BETWEEN 740010 AND 740019",
    "DELETE FROM users WHERE id BETWEEN 740100 AND 740199",
)
#: ``tiqora_*`` rows the tests of this band create besides the CMDB tables
_CMDB_JOBS = (
    "DELETE FROM tiqora_job_file WHERE job_id IN"
    " (SELECT id FROM tiqora_job WHERE kind LIKE 'core.cmdb_%')",
    "DELETE FROM tiqora_job WHERE kind LIKE 'core.cmdb_%'",
)
_TIQORA_CLEANUP = (
    *_CMDB_JOBS,
    "DELETE FROM tiqora_job_file WHERE job_id IN"
    " (SELECT id FROM tiqora_job WHERE created_by BETWEEN :lo AND :hi)",
    "DELETE FROM tiqora_job WHERE created_by BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_event_outbox WHERE ticket_id BETWEEN 740300 AND 740399",
    "DELETE FROM tiqora_cache_invalidation WHERE ticket_id BETWEEN 740300 AND 740399",
    "DELETE FROM tiqora_settings WHERE `key` LIKE 'itsm.%' OR `key` LIKE 'cmdb.%'",
)
#: FK-ordered: children first
_CMDB_TABLES = (
    "tiqora_itsm_link",
    "tiqora_cmdb_ci_attr",
    "tiqora_cmdb_ci_history",
    "tiqora_cmdb_attachment",
    "tiqora_cmdb_ci_version_archive",
    "tiqora_cmdb_ci_version",
    "tiqora_cmdb_ci",
    "tiqora_cmdb_import_template",
    "tiqora_cmdb_saved_search",
    "tiqora_cmdb_class_definition",
    "tiqora_cmdb_class",
)


def _q(sql: str, is_mysql: bool) -> str:
    return sql if is_mysql else sql.replace("`key`", '"key"')


def _delete_non_seed(conn: Any) -> None:
    """Delete catalog rows and link types that are not part of the seeds (children first)."""
    seed = {(r.catalog_class, r.item_key) for r in DEFAULT_CATALOG_ROWS}
    for cid, cls, key in conn.execute(
        text("SELECT id, catalog_class, item_key FROM tiqora_cmdb_catalog")
    ).all():
        if (cls, key) not in seed:
            conn.execute(text("DELETE FROM tiqora_cmdb_catalog WHERE id = :i"), {"i": cid})
    defaults = [d.name for d in DEFAULT_LINK_TYPES]
    for (name,) in conn.execute(
        text("SELECT name FROM tiqora_itsm_link_type WHERE origin = 'tiqora'")
    ).all():
        if name not in defaults:
            conn.execute(text("DELETE FROM tiqora_itsm_link_type WHERE name = :n"), {"n": name})


def cleanup_world(sync_url: str) -> None:
    """Delete every CMDB row, the band's Znuny/Tiqora rows, then drop the two views.

    Only this plan's tests write the ``tiqora_cmdb_*`` / ``tiqora_itsm_link*`` tables; the seeds
    are re-inserted by the next module's fixture. Link types of ``origin = 'znuny'`` are never
    touched.
    """
    is_mysql = "mysql" in sync_url
    engine = create_engine(sync_url)
    try:
        TiqoraBase.metadata.create_all(engine)
        with engine.begin() as conn:
            for table in _CMDB_TABLES:
                conn.execute(text(f"DELETE FROM {table}"))
            conn.execute(text("DELETE FROM tiqora_cmdb_catalog"))
            conn.execute(text("DELETE FROM tiqora_itsm_link_type WHERE origin = 'tiqora'"))
            for stmt in _TIQORA_CLEANUP:
                conn.execute(text(_q(stmt, is_mysql)), _P)
            for stmt in _ZNUNY_CLEANUP:
                conn.execute(text(stmt))
            drop_cmdb_views(conn)
    finally:
        engine.dispose()


def _seed_znuny_world(sync_url: str) -> None:
    engine = create_engine(sync_url)
    try:
        with engine.begin() as conn:
            for uid, (login, first, last) in _USERS.items():
                conn.execute(
                    text(
                        "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                        " create_time, create_by, change_time, change_by)"
                        " VALUES (:id, :l, 'x', :f, :ln, 1, :t, 1, :t, 1)"
                    ),
                    {"id": uid, "l": login, "f": first, "ln": last, "t": _T},
                )
            for gid, name in (
                (GROUP_RW, "cmdb4-rw"),
                (GROUP_RO, "cmdb4-ro"),
                (GROUP_HIDDEN, "cmdb4-hidden"),
            ):
                conn.execute(
                    text(
                        "INSERT INTO permission_groups (id, name, valid_id, create_time,"
                        " create_by, change_time, change_by) VALUES (:id, :n, 1, :t, 1, :t, 1)"
                    ),
                    {"id": gid, "n": name, "t": _T},
                )
            for qid, gid in ((QUEUE_RW, GROUP_RW), (QUEUE_HIDDEN, GROUP_HIDDEN)):
                conn.execute(
                    text(
                        "INSERT INTO queue (id, name, group_id, system_address_id,"
                        " salutation_id, signature_id, follow_up_id, follow_up_lock, valid_id,"
                        " create_time, create_by, change_time, change_by)"
                        " VALUES (:id, :n, :g, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
                    ),
                    {"id": qid, "n": f"cmdb4-queue-{qid}", "g": gid, "t": _T},
                )
            for sid, sname in ((SERVICE_ID, "cmdb4-service"), (SERVICE_ID_2, "cmdb4-service-2")):
                conn.execute(
                    text(
                        "INSERT INTO service (id, name, valid_id, create_time, create_by,"
                        " change_time, change_by) VALUES (:id, :n, 1, :t, 1, :t, 1)"
                    ),
                    {"id": sid, "n": sname, "t": _T},
                )
            admin_group = int(
                conn.execute(
                    text("SELECT id FROM permission_groups WHERE name = 'admin'")
                ).scalar_one()
            )
            grants = [
                (USER_A, GROUP_RW, "rw"),
                (USER_A, GROUP_RO, "ro"),
                (USER_B, GROUP_RO, "ro"),
                (USER_ADMIN, admin_group, "rw"),
            ]
            for uid, gid, key in grants:
                conn.execute(
                    text(
                        "INSERT INTO group_user (user_id, group_id, permission_key, create_time,"
                        " create_by, change_time, change_by)"
                        " VALUES (:u, :g, :k, :t, 1, :t, 1)"
                    ),
                    {"u": uid, "g": gid, "k": key, "t": _T},
                )
            for cid in ("cmdb4-co-a", "cmdb4-co-b", "cmdb4-co-c"):
                conn.execute(
                    text(
                        "INSERT INTO customer_company (customer_id, name, valid_id, create_time,"
                        " create_by, change_time, change_by) VALUES (:c, :n, 1, :t, 1, :t, 1)"
                    ),
                    {"c": cid, "n": f"{cid} Example", "t": _T},
                )
            for login, company in ((CUSTOMER_A, "cmdb4-co-a"), (CUSTOMER_B, "cmdb4-co-b")):
                conn.execute(
                    text(
                        "INSERT INTO customer_user (login, email, customer_id, first_name,"
                        " last_name, valid_id, create_time, create_by, change_time, change_by)"
                        " VALUES (:l, :l, :c, 'Cora', 'Customer', 1, :t, 1, :t, 1)"
                    ),
                    {"l": login, "c": company, "t": _T},
                )
            conn.execute(
                text(
                    "INSERT INTO customer_user_customer (user_id, customer_id, create_time,"
                    " create_by, change_time, change_by) VALUES (:u, 'cmdb4-co-c', :t, 1, :t, 1)"
                ),
                {"u": CUSTOMER_A, "t": _T},
            )
            states = {
                name: int(
                    conn.execute(
                        text("SELECT id FROM ticket_state WHERE name = :n"), {"n": name}
                    ).scalar_one()
                )
                for name in ("open", "closed successful")
            }
            tickets = (
                (TICKET_OPEN, QUEUE_RW, "open", CUSTOMER_A, "cmdb4-co-a"),
                (TICKET_HIDDEN, QUEUE_HIDDEN, "open", None, None),
                (TICKET_CLOSED, QUEUE_RW, "closed successful", None, None),
            )
            for tid, queue, state, cust_user, cust_id in tickets:
                conn.execute(
                    text(
                        "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                        " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                        " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                        " escalation_update_time, escalation_response_time,"
                        " escalation_solution_time, archive_flag,"
                        " create_time, create_by, change_time, change_by)"
                        " VALUES (:id, :tn, :title, :q, 1, 1, 1, 1, 3, :s, :cid, :cu,"
                        " 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
                    ),
                    {
                        "id": tid,
                        "tn": f"2026101074{tid}",
                        "title": f"cmdb4 ticket {tid}",
                        "q": queue,
                        "s": states[state],
                        "cid": cust_id,
                        "cu": cust_user,
                        "t": _T,
                    },
                )
    finally:
        engine.dispose()


async def _seed_cmdb(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as session:
        await seed_defaults(session)
        await session.commit()


@pytest.fixture(scope="module", params=["mariadb_znuny_url", "postgres_znuny_url"])
def cmdb_db(request: pytest.FixtureRequest) -> Iterator[CmdbWorld]:
    sync_url: str = request.getfixturevalue(request.param)
    cleanup_world(sync_url)  # also creates the tables and removes leftovers of a crashed run
    engine = create_engine(sync_url)
    try:
        with engine.begin() as conn:
            create_cmdb_views(conn)
    finally:
        engine.dispose()
    _seed_znuny_world(sync_url)
    # NullPool: the factory outlives the per-test event loops
    async_engine = create_async_engine(to_async_url(sync_url), poolclass=NullPool)
    factory = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    asyncio.run(_seed_cmdb(factory))
    world = CmdbWorld(
        factory=factory,
        sync_url=sync_url,
        dialect="mysql" if "mysql" in sync_url else "postgresql",
    )
    try:
        yield world
    finally:
        asyncio.run(async_engine.dispose())
        cleanup_world(sync_url)


@pytest.fixture(autouse=True)
def _cmdb_rows_reset(cmdb_db: CmdbWorld) -> Iterator[None]:
    """Delete every non-seed CMDB row and reset ``itsm.mode`` before each test."""
    is_mysql = cmdb_db.dialect == "mysql"
    engine = create_engine(cmdb_db.sync_url)
    try:
        with engine.begin() as conn:
            for table in _CMDB_TABLES:
                conn.execute(text(f"DELETE FROM {table}"))
            _delete_non_seed(conn)
            for stmt in _CMDB_JOBS:
                conn.execute(text(stmt))
            conn.execute(
                text(_q("DELETE FROM tiqora_settings WHERE `key` = 'itsm.mode'", is_mysql))
            )
    finally:
        engine.dispose()
    yield


# --- helpers ----------------------------------------------------------------------------


async def make_class(
    session: AsyncSession,
    *,
    key: str = "cmdb4srv",
    group_id: int = GROUP_RW,
    attributes: list[dict[str, Any]] | None = None,
    portal_visible: bool = False,
) -> int:
    """Create a class with definition 1 through ``ClassService`` and commit; returns its id."""
    svc = ClassService(session)
    cls = await svc.create(
        ClassData(class_key=key, name=key, group_id=group_id, portal_visible=portal_visible),
        user_id=USER_ADMIN,
    )
    await svc.add_definition(
        cls.id,
        parse_attrs(SERVER_ATTRS if attributes is None else attributes),
        comment=None,
        user_id=USER_ADMIN,
    )
    await session.commit()
    return cls.id


async def make_ci(
    session: AsyncSession,
    class_id: int,
    name: str,
    *,
    user_id: int = USER_A,
    attributes: dict[str, Any] | None = None,
    deployment: str = "production",
    incident: str = "operational",
) -> int:
    """Create a CI through ``CiService`` (system access, no permission checks) and commit."""
    from tiqora.cmdb.service import CiService, system_access

    rec = await CiService(session).create(
        system_access(user_id),
        class_id=class_id,
        name=name,
        deployment_state_id=await _dep(session, deployment),
        incident_state_id=await _inc(session, incident),
        attributes=attributes or {},
        enforce_permissions=False,
    )
    await session.commit()
    return rec.id


async def make_link_type(
    session: AsyncSession, name: str, propagation: str, *, pointed: bool = True
) -> None:
    """A test-own ``origin="tiqora"`` link type (offered for CI-CI and CI-service) and commit.

    ``_cmdb_rows_reset`` removes it again (non-default Tiqora-origin rows)."""
    from tiqora.itsm.models import TiqoraItsmLinkType

    session.add(
        TiqoraItsmLinkType(
            name=name,
            origin="tiqora",
            source_label=None,
            target_label=None,
            pointed=pointed,
            propagation=propagation,
            offer_ticket_ticket=False,
            offer_ticket_ci=False,
            offer_ci_ci=True,
            offer_ci_service=True,
            position=900,
            valid=True,
            create_time=_T,
            create_by=USER_ADMIN,
            change_time=_T,
            change_by=USER_ADMIN,
        )
    )
    await session.commit()


async def _dep(session: AsyncSession, key: str) -> int:
    """Catalog id of the deployment state with ``item_key == key``."""
    return await _catalog_id(session, DEPLOYMENT_STATE, key)


async def _inc(session: AsyncSession, key: str) -> int:
    """Catalog id of the incident state with ``item_key == key``."""
    return await _catalog_id(session, INCIDENT_STATE, key)


async def _catalog_id(session: AsyncSession, catalog_class: str, key: str) -> int:
    row = await session.execute(
        select(TiqoraCmdbCatalog.id).where(
            TiqoraCmdbCatalog.catalog_class == catalog_class,
            TiqoraCmdbCatalog.item_key == key,
        )
    )
    return int(row.scalar_one())


async def run_jobs_until_idle(world: CmdbWorld, *, max_rounds: int = 20) -> None:
    """Drive ``JobRunner`` (as ``tests/test_reports_api.py`` does) until no job is queued."""
    runner = JobRunner(
        session_factory=world.factory,
        settings=Settings(environment="test"),
        registry=get_registry(),
    )
    for _ in range(max_rounds):
        started = (await runner.run_once())["started"]
        await runner.wait_idle()
        if not started:
            return


def _agent(uid: int) -> AuthenticatedUser:
    login, first, last = _USERS[uid]
    return AuthenticatedUser(
        id=uid, login=login, first_name=first, last_name=last, auth_method="session"
    )


def client_for(world: CmdbWorld, user_id: int) -> AsyncClient:
    """An ``AsyncClient`` acting as ``user_id`` (use as ``async with client_for(...) as c``)."""
    factory = world.factory

    async def _db() -> Any:
        async with factory() as session:
            yield session

    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_session_factory_dep] = lambda: factory
    app.dependency_overrides[get_current_user] = lambda: _agent(user_id)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


_CUSTOMER_COMPANY = {CUSTOMER_A: "cmdb4-co-a", CUSTOMER_B: "cmdb4-co-b"}


def portal_client_for(world: CmdbWorld, login: str) -> AsyncClient:
    """An ``AsyncClient`` on the customer portal acting as ``login`` (portal switched on)."""
    from tiqora.api.portal.deps import (
        get_current_customer,
        get_portal_ticket_service,
        require_portal_enabled,
    )
    from tiqora.domain.customer_auth import AuthenticatedCustomer
    from tiqora.domain.portal_ticket_service import PortalTicketService
    from tiqora.znuny.sysconfig import SysConfig

    factory = world.factory

    async def _db() -> Any:
        async with factory() as session:
            yield session

    async def _fetch(name: str) -> Any:
        return None

    async def _svc(session: Annotated[AsyncSession, Depends(get_db)]) -> PortalTicketService:
        return PortalTicketService(session, factory, SysConfig(fetch=_fetch))

    customer = AuthenticatedCustomer(
        id=1,
        login=login,
        email=login,
        customer_id=_CUSTOMER_COMPANY[login],
        first_name="Cora",
        last_name="Customer",
    )
    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_session_factory_dep] = lambda: factory
    app.dependency_overrides[require_portal_enabled] = lambda: None
    app.dependency_overrides[get_current_customer] = lambda: customer
    app.dependency_overrides[get_portal_ticket_service] = _svc
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def set_mode(world: CmdbWorld, mode: str) -> None:
    """Write ``tiqora_settings`` ``itsm.mode`` (the bare string)."""
    is_mysql = world.dialect == "mysql"
    engine = create_engine(world.sync_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM tiqora_settings WHERE "
                    + ("`key`" if is_mysql else '"key"')
                    + " = 'itsm.mode'"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO tiqora_settings ("
                    + ("`key`" if is_mysql else '"key"')
                    + ", value) VALUES ('itsm.mode', :m)"
                ),
                {"m": mode},
            )
    finally:
        engine.dispose()
