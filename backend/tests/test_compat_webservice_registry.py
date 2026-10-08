"""WebserviceRegistry against real ``gi_webservice_config`` rows (MariaDB and PostgreSQL).

Seed ids use 8900-8919 (GI band); every row is deleted again after the module.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import datetime
from typing import Any

import pytest
import yaml
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.api.compat.webservices import WebserviceRegistry, get_registry, load_fingerprint
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

T0 = datetime(2026, 1, 1, 10, 0, 0)
T1 = datetime(2026, 1, 2, 10, 0, 0)

VALID_ID, DISABLED_ID, BROKEN_ID, MAPPING_ID = 8900, 8901, 8902, 8903

REST_CFG: dict[str, Any] = {
    "Provider": {
        "Transport": {
            "Type": "HTTP::REST",
            "Config": {
                "RouteOperationMapping": {
                    "TicketGet": {"Route": "/Ticket/:TicketID", "RequestMethod": ["GET"]}
                }
            },
        },
        "Operation": {"TicketGet": {"Type": "Ticket::TicketGet"}},
    }
}


def _async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)


def _seed_statements() -> tuple[tuple[str, dict[str, Any]], ...]:
    return (("DELETE FROM gi_webservice_config WHERE id BETWEEN 8900 AND 8919", {}),)


_SEEDED: list[str] = []


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


def _insert(
    sync_url: str,
    ws_id: int,
    name: str,
    config: bytes,
    *,
    valid_id: int = 1,
    changed: datetime = T0,
) -> None:
    engine = create_engine(sync_url)
    # PostgreSQL stores `config` as TEXT (a bytes bind would be stored as its hex form).
    stored: bytes | str = config.decode() if engine.dialect.name == "postgresql" else config
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO gi_webservice_config (id, name, config, valid_id, create_time,"
                " create_by, change_time, change_by)"
                " VALUES (:id, :name, :config, :valid, :t, 1, :c, 1)"
            ),
            {"id": ws_id, "name": name, "config": stored, "valid": valid_id, "t": T0, "c": changed},
        )
    engine.dispose()


def _exec(sync_url: str, sql: str, **params: Any) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(text(sql), params)
    engine.dispose()


def _seed(sync_url: str) -> None:
    _SEEDED.append(sync_url)
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        for stmt, params in _seed_statements():
            conn.execute(text(stmt), params)
    engine.dispose()
    _insert(sync_url, VALID_ID, "RegistryRest", yaml.safe_dump(REST_CFG).encode())
    _insert(
        sync_url, DISABLED_ID, "RegistryDisabled", yaml.safe_dump(REST_CFG).encode(), valid_id=2
    )


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
def sync_url(request: pytest.FixtureRequest) -> str:
    url: str = request.getfixturevalue(request.param)
    _seed(url)
    return url


async def _with_session(sync_url: str, fn: Any) -> Any:
    engine = create_async_engine(_async_url(sync_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            return await fn(session)
    finally:
        await engine.dispose()


async def test_registry_loads_valid_rows_and_fingerprint_changes(sync_url: str) -> None:
    registry = WebserviceRegistry()
    assert registry.loaded is False and registry.fingerprint is None

    async def first(session: AsyncSession) -> None:
        assert await registry.refresh(session) is True
        assert registry.loaded is True
        ws = registry.by_name("RegistryRest")
        assert ws is not None and ws.id == VALID_ID
        assert registry.by_id(VALID_ID) is ws
        assert registry.by_name("RegistryDisabled") is None
        assert registry.by_id(DISABLED_ID) is None
        assert ws.transport == "HTTP::REST"
        assert ws.operations["TicketGet"].handler == "TicketGet"
        # same fingerprint: nothing is reloaded and the snapshot object stays the same
        assert await registry.refresh(session) is False
        assert registry.by_name("RegistryRest") is ws

    await _with_session(sync_url, first)
    before = registry.fingerprint
    assert before is not None and before[0] >= 2 and before[2] >= DISABLED_ID

    _exec(
        sync_url,
        "UPDATE gi_webservice_config SET change_time = :t WHERE id = :id",
        t=T1,
        id=VALID_ID,
    )

    async def second(session: AsyncSession) -> None:
        old = registry.by_name("RegistryRest")
        assert await registry.refresh(session) is True
        assert registry.fingerprint != before
        new = registry.by_name("RegistryRest")
        assert new is not None and new is not old
        assert await registry.refresh(session) is False

    await _with_session(sync_url, second)


async def test_registry_skips_unparsable_row(sync_url: str) -> None:
    _insert(sync_url, BROKEN_ID, "RegistryBroken", b"Provider: {a: [1, 2}")
    registry = WebserviceRegistry()

    async def run(session: AsyncSession) -> None:
        await registry.refresh(session)
        assert registry.by_id(BROKEN_ID) is None
        assert registry.by_name("RegistryBroken") is None
        assert registry.by_id(VALID_ID) is not None

    await _with_session(sync_url, run)


async def test_row_without_provider_is_kept_with_empty_transport(sync_url: str) -> None:
    _insert(sync_url, BROKEN_ID, "RegistryNoProvider", b"::: not yaml [")  # valid YAML mapping
    registry = WebserviceRegistry()

    async def run(session: AsyncSession) -> None:
        await registry.refresh(session)
        ws = registry.by_id(BROKEN_ID)
        assert ws is not None and ws.transport == "" and ws.routes == ()
        assert registry.by_name("RegistryNoProvider").id == BROKEN_ID  # type: ignore[union-attr]
        assert "RegistryNoProvider" in registry.names()

    await _with_session(sync_url, run)


async def test_sysconfig_failure_keeps_previous_snapshot_and_fingerprint(
    sync_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = WebserviceRegistry()

    async def first(session: AsyncSession) -> None:
        await registry.refresh(session)

    await _with_session(sync_url, first)
    old_fp, old_ws = registry.fingerprint, registry.by_name("RegistryRest")
    assert old_ws is not None

    cfg = yaml.safe_load(yaml.safe_dump(REST_CFG))
    cfg["Provider"]["Transport"]["Config"]["Base"] = "https://<OTRS_CONFIG_FQDN>/x"
    _insert(sync_url, MAPPING_ID, "RegistryPlaceholder", yaml.safe_dump(cfg).encode())

    original = SysConfig.get

    async def boom(self: SysConfig, name: str, default: Any = None) -> Any:
        if name == "FQDN":
            raise RuntimeError("db went away")
        return await original(self, name, default)

    monkeypatch.setattr(SysConfig, "get", boom)

    async def second(session: AsyncSession) -> None:
        with pytest.raises(RuntimeError):
            await registry.refresh(session)

    await _with_session(sync_url, second)
    assert registry.fingerprint == old_fp
    assert registry.by_name("RegistryRest") is old_ws
    assert registry.by_name("RegistryPlaceholder") is None
    monkeypatch.undo()

    async def third(session: AsyncSession) -> None:
        assert await registry.refresh(session) is True  # recovers on the next tick
        assert registry.by_name("RegistryPlaceholder") is not None

    await _with_session(sync_url, third)


async def test_same_second_edit_of_another_row_changes_fingerprint(sync_url: str) -> None:
    _insert(sync_url, MAPPING_ID, "RegistryOther", yaml.safe_dump(REST_CFG).encode())

    async def fp(session: AsyncSession) -> Any:
        return await load_fingerprint(session)

    before = await _with_session(sync_url, fp)
    # a lower-id row changes to a time that is NOT the newest: max(change_time) alone would miss it
    _exec(
        sync_url,
        "UPDATE gi_webservice_config SET change_time = :t WHERE id = :id",
        t=T0.replace(minute=5),
        id=VALID_ID,
    )
    after = await _with_session(sync_url, fp)
    assert before != after
    # valid_id values that trade places keep count, max(id) and sum(valid_id) - digest still differs
    _exec(sync_url, "UPDATE gi_webservice_config SET valid_id = 2 WHERE id = :id", id=VALID_ID)
    _exec(sync_url, "UPDATE gi_webservice_config SET valid_id = 1 WHERE id = :id", id=DISABLED_ID)
    swapped = await _with_session(sync_url, fp)
    assert swapped[0] == after[0] and swapped[2:] == after[2:]
    assert swapped != after


async def test_registry_skips_non_mapping_yaml(sync_url: str) -> None:
    _insert(sync_url, BROKEN_ID, "RegistryList", b"- just\n- a list\n")
    registry = WebserviceRegistry()

    async def run(session: AsyncSession) -> None:
        await registry.refresh(session)
        assert registry.by_id(BROKEN_ID) is None
        assert registry.by_id(VALID_ID) is not None

    await _with_session(sync_url, run)


async def test_unknown_mapping_type_keeps_service_served_with_error(sync_url: str) -> None:
    cfg = yaml.safe_load(yaml.safe_dump(REST_CFG))
    cfg["Provider"]["Operation"]["TicketGet"]["MappingInbound"] = {
        "Type": "Znuny",
        "Config": {"a": 1},
    }
    _insert(sync_url, MAPPING_ID, "RegistryBadMapping", yaml.safe_dump(cfg).encode())
    registry = WebserviceRegistry()

    async def run(session: AsyncSession) -> None:
        await registry.refresh(session)
        ws = registry.by_name("RegistryBadMapping")
        assert ws is not None
        assert "Znuny" in (ws.operations["TicketGet"].mapping_error or "")
        assert registry.by_name("RegistryRest") is not None

    await _with_session(sync_url, run)


async def test_delete_row_changes_fingerprint(sync_url: str) -> None:
    registry = WebserviceRegistry()

    async def first(session: AsyncSession) -> None:
        await registry.refresh(session)
        assert registry.by_id(VALID_ID) is not None

    await _with_session(sync_url, first)
    before = registry.fingerprint
    _exec(sync_url, "DELETE FROM gi_webservice_config WHERE id = :id", id=VALID_ID)

    async def second(session: AsyncSession) -> None:
        assert await registry.refresh(session) is True
        assert registry.fingerprint != before
        assert registry.by_name("RegistryRest") is None

    await _with_session(sync_url, second)


async def test_disabling_row_is_picked_up(sync_url: str) -> None:
    registry = WebserviceRegistry()

    async def first(session: AsyncSession) -> None:
        await registry.refresh(session)

    await _with_session(sync_url, first)
    _exec(sync_url, "UPDATE gi_webservice_config SET valid_id = 2 WHERE id = :id", id=VALID_ID)

    async def second(session: AsyncSession) -> None:
        assert await registry.refresh(session) is True
        assert registry.by_name("RegistryRest") is None

    await _with_session(sync_url, second)


async def test_ensure_loaded_loads_once_and_force_reloads(sync_url: str) -> None:
    registry = WebserviceRegistry()

    async def run(session: AsyncSession) -> None:
        await registry.ensure_loaded(session)
        assert registry.loaded is True
        ws = registry.by_name("RegistryRest")
        assert ws is not None
        await registry.ensure_loaded(session)
        assert registry.by_name("RegistryRest") is ws
        assert await registry.refresh(session, force=True) is True
        assert registry.by_name("RegistryRest") is not ws
        fp = await load_fingerprint(session)
        assert fp == registry.fingerprint

    await _with_session(sync_url, run)


def test_get_registry_is_created_lazily_per_app() -> None:
    class State:
        pass

    class App:
        state = State()

    first = get_registry(App)
    assert isinstance(first, WebserviceRegistry)
    assert get_registry(App) is first
