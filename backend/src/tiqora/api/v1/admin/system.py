"""Admin API for the "System-Info" page — ``/api/v1/admin/system``.

A single read-only aggregate that gives an admin a complete picture of the
installation: application/build identity, background-daemon health (reused from
``daemons.py``), datastore + search status, running containers, and host
resource utilisation.

Every probe is best-effort and degrades gracefully — a missing optional
dependency (``docker`` / ``psutil``), an unmounted docker socket, or an
unreachable datastore turns into ``available=False`` / ``connected=False`` with
a human ``reason`` instead of a 500. That keeps the page useful even on a
half-configured box and keeps the endpoint importable in test/dev where none of
those are present.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import platform
import socket as _socket
import time
from datetime import UTC, datetime
from typing import Annotated

import redis.asyncio as _redis
import structlog
from fastapi import APIRouter, Depends
from sqlalchemy import text

from tiqora.api.deps import AppSettings, DbSession, get_redis
from tiqora.api.v1.admin.daemons import _raw_settings, _to_out
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.api.v1.admin.schemas import (
    AppInfoOut,
    ContainerOut,
    ContainersOut,
    DatastoresOut,
    DbStatusOut,
    HostOut,
    LegacySchemaOut,
    RedisStatusOut,
    SearchStatusOut,
    SystemInfoOut,
)
from tiqora.config import Settings
from tiqora.worker.services import DAEMON_SERVICES

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/system", tags=["admin:system"])

#: Process start — approximates API uptime (module import ≈ process boot).
_STARTED_AT = datetime.now(UTC)

#: Root filesystem is the one worth watching (Docker layers, logs live here).
_DISK_PATH = "/"


def _app_info(cfg: Settings) -> AppInfoOut:
    from tiqora import __version__

    now = datetime.now(UTC)
    return AppInfoOut(
        name=cfg.app_name,
        version=__version__,
        git_sha=cfg.git_sha or None,
        build_time=cfg.build_time or None,
        environment=cfg.environment,
        python_version=platform.python_version(),
        hostname=_socket.gethostname(),
        server_time=now,
        started_at=_STARTED_AT,
        uptime_seconds=(now - _STARTED_AT).total_seconds(),
    )


async def _legacy_schema_out(session: DbSession, cfg: Settings) -> LegacySchemaOut | None:
    """Return the cached schema profile, or detect on the fly for this request."""
    from tiqora.db.legacy.profile import (
        detect_legacy_schema_profile,
        get_legacy_schema_profile,
        profile_for_id,
    )

    cached = get_legacy_schema_profile()
    if cached is not None:
        return LegacySchemaOut(**cached.to_public_dict())

    if cfg.legacy_schema_profile.strip():
        try:
            dialect = "postgresql" if cfg.is_postgres else "mysql" if cfg.is_mysql else "unknown"
            forced = profile_for_id(
                cfg.legacy_schema_profile.strip(), dialect=dialect, source="override"
            )
            return LegacySchemaOut(**forced.to_public_dict())
        except ValueError:
            pass

    try:
        detected = await detect_legacy_schema_profile(session)
        return LegacySchemaOut(**detected.to_public_dict())
    except Exception as exc:  # noqa: BLE001
        logger.warning("sysinfo_legacy_schema_probe_failed", error=str(exc))
        return None


async def _db_status(session: DbSession, cfg: Settings) -> DbStatusOut:
    dialect = "postgresql" if cfg.is_postgres else "mysql" if cfg.is_mysql else "unknown"
    out = DbStatusOut(dialect=dialect, connected=False)
    try:
        start = time.perf_counter()
        await session.execute(text("SELECT 1"))
        out.latency_ms = round((time.perf_counter() - start) * 1000, 2)
        out.connected = True
    except Exception as exc:  # noqa: BLE001 — status probe never raises
        logger.warning("sysinfo_db_probe_failed", error=str(exc))
        return out

    with contextlib.suppress(Exception):
        out.version = str((await session.execute(text("SELECT version()"))).scalar_one())

    try:
        if cfg.is_postgres:
            size_sql = "SELECT pg_database_size(current_database())"
        else:
            size_sql = (
                "SELECT COALESCE(SUM(data_length + index_length), 0) "
                "FROM information_schema.tables WHERE table_schema = DATABASE()"
            )
        out.size_bytes = int((await session.execute(text(size_sql))).scalar_one())
    except Exception:  # noqa: BLE001
        pass

    out.legacy_schema = await _legacy_schema_out(session, cfg)
    return out


async def _redis_status(client: _redis.Redis) -> RedisStatusOut:
    out = RedisStatusOut(connected=False)
    try:
        start = time.perf_counter()
        pong = await client.ping()
        out.latency_ms = round((time.perf_counter() - start) * 1000, 2)
        out.connected = bool(pong)
    except Exception as exc:  # noqa: BLE001
        logger.warning("sysinfo_redis_probe_failed", error=str(exc))
        return out

    try:
        info = await client.info()
        out.version = info.get("redis_version")
        mem = info.get("used_memory")
        out.used_memory_bytes = int(mem) if mem is not None else None
        clients = info.get("connected_clients")
        out.clients = int(clients) if clients is not None else None
    except Exception:  # noqa: BLE001
        pass

    return out


async def _search_status(cfg: Settings) -> SearchStatusOut:
    try:
        from meilisearch_python_sdk import AsyncClient
    except ImportError:
        return SearchStatusOut(available=False, reason="meilisearch SDK nicht installiert")

    client = AsyncClient(url=cfg.meili_url, api_key=cfg.meili_master_key)
    out = SearchStatusOut(available=False)
    try:
        try:
            health = await client.health()
            status_val = getattr(health, "status", None) or (
                health.get("status") if isinstance(health, dict) else None
            )
            out.available = status_val == "available"
        except Exception as exc:  # noqa: BLE001
            out.reason = f"nicht erreichbar: {exc}"
            return out

        try:
            ver = await client.get_version()
            out.version = getattr(ver, "pkg_version", None) or (
                ver.get("pkgVersion") if isinstance(ver, dict) else None
            )
        except Exception:  # noqa: BLE001
            pass

        try:
            stats = await client.get_all_stats()
            out.database_size_bytes = getattr(stats, "database_size", None)
            indexes = getattr(stats, "indexes", None) or {}
            tickets = indexes.get(cfg.meili_tickets_index)
            kb = indexes.get(cfg.meili_kb_index)
            if tickets is not None:
                out.tickets_docs = getattr(tickets, "number_of_documents", None)
            if kb is not None:
                out.kb_docs = getattr(kb, "number_of_documents", None)
        except Exception:  # noqa: BLE001
            pass

        return out
    finally:
        # AsyncClient holds an httpx client; close it so we don't leak sockets.
        with contextlib.suppress(Exception):
            await client.aclose()


#: Default docker socket path — its presence tells us the opt-in is set up.
_DOCKER_SOCK = "/var/run/docker.sock"


def _docker_configured() -> bool:
    """Whether the docker opt-in is even set up (socket mounted or DOCKER_HOST)."""
    return bool(os.environ.get("DOCKER_HOST")) or os.path.exists(_DOCKER_SOCK)


#: docker-compose stamps every container it manages with this label.
_COMPOSE_PROJECT_LABEL = "com.docker.compose.project"


def _own_compose_project(client: object) -> str | None:
    """Read this container's own compose-project label, so the probe can scope
    itself to Tiqora's stack instead of every container on a shared host."""
    with contextlib.suppress(Exception):
        me = client.containers.get(_socket.gethostname())  # type: ignore[attr-defined]
        return (me.labels or {}).get(_COMPOSE_PROJECT_LABEL)
    return None


def _collect_containers_sync(project_override: str = "") -> ContainersOut:
    """Blocking docker probe — run via ``asyncio.to_thread``.

    Only containers of *our* docker-compose project are returned: on a shared
    host (e.g. next to the Znuny stack) listing every container would leak
    unrelated services into Tiqora's admin view. The project is taken from
    ``project_override`` (``TIQORA_DOCKER_PROJECT``) or auto-detected from this
    container's own compose label; if neither is available (bare ``docker run``
    / local dev) the probe falls back to listing everything.
    """
    try:
        import docker
    except ImportError:
        # SDK absent → opt-in not set up, not an error.
        return ContainersOut(available=False, configured=False)

    if not _docker_configured():
        # No socket mounted / no DOCKER_HOST → the feature simply isn't enabled.
        return ContainersOut(available=False, configured=False)

    try:
        client = docker.from_env()
    except Exception as exc:  # noqa: BLE001
        return ContainersOut(available=False, reason=f"Docker-Socket nicht erreichbar: {exc}")

    engine_version: str | None = None
    with contextlib.suppress(Exception):
        engine_version = client.version().get("Version")

    project = project_override.strip() or _own_compose_project(client)
    list_filters = {"label": f"{_COMPOSE_PROJECT_LABEL}={project}"} if project else None

    items: list[ContainerOut] = []
    try:
        for c in client.containers.list(all=True, filters=list_filters):
            attrs = c.attrs or {}
            state = attrs.get("State", {}) or {}
            image_tags = getattr(c.image, "tags", None) or []
            image = image_tags[0] if image_tags else getattr(c.image, "short_id", "") or ""
            started_raw = state.get("StartedAt")
            started_at = None
            if isinstance(started_raw, str) and started_raw and not started_raw.startswith("0001"):
                try:
                    started_at = datetime.fromisoformat(started_raw.replace("Z", "+00:00"))
                except ValueError:
                    started_at = None
            items.append(
                ContainerOut(
                    name=c.name,
                    image=image,
                    state=c.status,
                    health=(state.get("Health") or {}).get("Status"),
                    started_at=started_at,
                    restart_count=attrs.get("RestartCount"),
                )
            )
    except Exception as exc:  # noqa: BLE001
        return ContainersOut(available=False, reason=f"Container-Abfrage fehlgeschlagen: {exc}")
    finally:
        with contextlib.suppress(Exception):
            client.close()

    items.sort(key=lambda i: i.name)
    return ContainersOut(available=True, engine_version=engine_version, items=items)


def _collect_host_sync() -> HostOut:
    """Blocking psutil probe — run via ``asyncio.to_thread``."""
    try:
        import psutil
    except ImportError:
        # Optional dependency absent → opt-in not set up, not an error.
        return HostOut(available=False, configured=False)

    try:
        vm = psutil.virtual_memory()
        du = psutil.disk_usage(_DISK_PATH)
        try:
            load = list(psutil.getloadavg())
        except (AttributeError, OSError):
            load = None
        return HostOut(
            available=True,
            cpu_percent=psutil.cpu_percent(interval=0.15),
            cpu_count=psutil.cpu_count(),
            load_avg=[round(x, 2) for x in load] if load else None,
            memory_total_bytes=vm.total,
            memory_used_bytes=vm.total - vm.available,
            memory_percent=vm.percent,
            disk_path=_DISK_PATH,
            disk_total_bytes=du.total,
            disk_used_bytes=du.used,
            disk_percent=du.percent,
        )
    except Exception as exc:  # noqa: BLE001
        return HostOut(available=False, reason=f"psutil-Abfrage fehlgeschlagen: {exc}")


@router.get("", response_model=SystemInfoOut)
async def get_system_info(
    admin: AdminUser,
    session: DbSession,
    cfg: AppSettings,
    redis_client: Annotated[_redis.Redis, Depends(get_redis)],
) -> SystemInfoOut:
    _ = admin

    raw = await _raw_settings(session)
    services = [_to_out(svc, raw) for svc in DAEMON_SERVICES]

    database, redis_status, search, containers, host = await asyncio.gather(
        _db_status(session, cfg),
        _redis_status(redis_client),
        _search_status(cfg),
        asyncio.to_thread(_collect_containers_sync, cfg.docker_project),
        asyncio.to_thread(_collect_host_sync),
    )

    return SystemInfoOut(
        app=_app_info(cfg),
        services=services,
        datastores=DatastoresOut(database=database, redis=redis_status, search=search),
        containers=containers,
        host=host,
    )
