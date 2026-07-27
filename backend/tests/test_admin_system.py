"""DB test for the admin "System-Info" API (GET ``/api/v1/admin/system``).

Follows the direct-service-call pattern from ``test_admin_daemons.py`` (local
testcontainer only, never Prod). The endpoint is a best-effort aggregate: the
datastore probe runs against the real container, while Redis/Docker/psutil
degrade gracefully — so this asserts structure and the DB probe, and only that
the optional sections are well-formed (available flag + reason), never that a
particular optional backend is reachable in CI.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tiqora.api.v1.admin import system as admin_system
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.auth import AuthenticatedUser
from tiqora.worker.services import DAEMON_SERVICES

pytestmark = pytest.mark.db


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


def _ensure_tiqora_tables(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        conn.execute(text("DELETE FROM tiqora_settings"))
    engine.dispose()


def _root_user() -> AuthenticatedUser:
    return AuthenticatedUser(
        id=1, login="root@localhost", first_name="Admin", last_name="Znuny", auth_method="session"
    )


class _DeadRedis:
    """Stand-in whose calls fail, so the Redis probe reports connected=False."""

    async def ping(self) -> bool:
        raise ConnectionError("no redis in test")

    async def info(self) -> dict[str, str]:
        raise ConnectionError("no redis in test")


async def test_get_system_info_aggregate(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    async_url = _mysql_async(mariadb_znuny_url)
    cfg = Settings(database_url=async_url)  # so dialect detection sees MySQL/Maria
    engine = create_async_engine(async_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            out = await admin_system.get_system_info(
                admin=_root_user(),
                session=session,
                cfg=cfg,
                redis_client=_DeadRedis(),  # type: ignore[arg-type]
            )

            # App identity is always populated.
            assert out.app.name == cfg.app_name
            assert out.app.version
            assert out.app.python_version
            assert out.app.uptime_seconds >= 0

            # Services reuse the daemon catalog (one row per service).
            assert len(out.services) == len(DAEMON_SERVICES)

            # Database probe ran against the real container.
            assert out.datastores.database.connected is True
            assert out.datastores.database.dialect == "mysql"
            assert out.datastores.database.latency_ms is not None
            # OTRS/Znuny schema profile (Znuny 6.5 fixture).
            legacy = out.datastores.database.legacy_schema
            assert legacy is not None
            assert legacy.known is True
            assert legacy.groups_table == "permission_groups"
            assert legacy.profile_id == "znuny-6.5"

            # Redis degrades gracefully (stub raises).
            assert out.datastores.redis.connected is False

            # Optional sections are well-formed regardless of availability.
            assert isinstance(out.datastores.search.available, bool)
            assert isinstance(out.containers.available, bool)
            assert isinstance(out.containers.items, list)
            assert isinstance(out.host.available, bool)
            # When a section is unavailable it either isn't set up
            # (configured=False) or explains the error (reason set).
            if not out.containers.available:
                assert out.containers.reason or out.containers.configured is False
            if not out.host.available:
                assert out.host.reason or out.host.configured is False
    finally:
        await engine.dispose()


class _FakeContainer:
    def __init__(self, name: str, labels: dict[str, str]) -> None:
        self.name = name
        self.labels = labels
        self.status = "running"
        self.attrs: dict[str, object] = {"State": {}}
        self.image = type("Img", (), {"tags": ["img:latest"], "short_id": "abc"})()


class _FakeContainers:
    def __init__(self, me: _FakeContainer, all_items: list[_FakeContainer]) -> None:
        self._me = me
        self._all = all_items
        self.last_filters: dict[str, str] | None = None

    def get(self, _ident: str) -> _FakeContainer:
        return self._me

    def list(self, all: bool = False, filters: dict[str, str] | None = None):  # noqa: A002
        self.last_filters = filters
        if not filters:
            return self._all
        label = filters["label"]
        key, _, value = label.partition("=")
        return [c for c in self._all if c.labels.get(key) == value]


class _FakeDockerClient:
    def __init__(self, containers: _FakeContainers) -> None:
        self.containers = containers

    def version(self) -> dict[str, str]:
        return {"Version": "27.0.0"}

    def close(self) -> None:
        pass


def test_containers_probe_scopes_to_own_compose_project(monkeypatch: pytest.MonkeyPatch) -> None:
    """On a shared host, only the API container's own compose project shows."""
    label = "com.docker.compose.project"
    me = _FakeContainer("tiqora-api", {label: "tiqora"})
    others = [
        me,
        _FakeContainer("tiqora-worker", {label: "tiqora"}),
        _FakeContainer("znuny-web", {label: "otrs65"}),
        _FakeContainer("random-thing", {}),
    ]
    fake_containers = _FakeContainers(me, others)
    fake_client = _FakeDockerClient(fake_containers)

    # Pretend the opt-in is set up and hand the probe our fake docker client.
    monkeypatch.setattr(admin_system, "_docker_configured", lambda: True)
    fake_docker = type("M", (), {"from_env": staticmethod(lambda: fake_client)})
    monkeypatch.setitem(__import__("sys").modules, "docker", fake_docker)

    out = admin_system._collect_containers_sync()

    assert out.available is True
    assert out.engine_version == "27.0.0"
    # Self-detected project=tiqora → only the two tiqora containers, not znuny/random.
    assert fake_containers.last_filters == {"label": f"{label}=tiqora"}
    names = {c.name for c in out.items}
    assert names == {"tiqora-api", "tiqora-worker"}


def test_containers_probe_env_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """TIQORA_DOCKER_PROJECT overrides auto-detection."""
    label = "com.docker.compose.project"
    me = _FakeContainer("tiqora-api", {label: "tiqora"})
    fake_containers = _FakeContainers(me, [me])
    monkeypatch.setattr(admin_system, "_docker_configured", lambda: True)
    fake_docker = type(
        "M", (), {"from_env": staticmethod(lambda: _FakeDockerClient(fake_containers))}
    )
    monkeypatch.setitem(__import__("sys").modules, "docker", fake_docker)

    admin_system._collect_containers_sync("custom-project")

    assert fake_containers.last_filters == {"label": f"{label}=custom-project"}
