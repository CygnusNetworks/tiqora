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


def _collect_containers_sync() -> ContainersOut:
    """Blocking docker probe — run via ``asyncio.to_thread``."""
    try:
        import docker
    except ImportError:
        return ContainersOut(available=False, reason="docker SDK nicht installiert")

    try:
        client = docker.from_env()
    except Exception as exc:  # noqa: BLE001
        return ContainersOut(
            available=False,
            reason=f"Docker-Socket nicht erreichbar (nicht gemountet?): {exc}",
        )

    items: list[ContainerOut] = []
    try:
        for c in client.containers.list(all=True):
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
    return ContainersOut(available=True, items=items)


def _collect_host_sync() -> HostOut:
    """Blocking psutil probe — run via ``asyncio.to_thread``."""
    try:
        import psutil
    except ImportError:
        return HostOut(available=False, reason="psutil nicht installiert")

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
        asyncio.to_thread(_collect_containers_sync),
        asyncio.to_thread(_collect_host_sync),
    )

    return SystemInfoOut(
        app=_app_info(cfg),
        services=services,
        datastores=DatastoresOut(database=database, redis=redis_status, search=search),
        containers=containers,
        host=host,
    )
