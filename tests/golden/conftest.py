"""Shared fixtures for the golden-master suite.

These tests compare a REAL OTRS/Znuny peer container (default Znuny 6.5.22;
select with ``GOLDEN_PEER`` — see ``peers.yaml``) against Tiqora on the SAME
MariaDB database. They are opt-in and require:

- Docker running locally with the golden stack up (``just golden-up`` +
  ``just golden-seed``).
- ``GOLDEN=1`` in the environment.

All tests are marked ``golden`` and are skipped by default (both the
``not db``-style default pytest run and CI) unless ``GOLDEN=1`` is set,
since bringing up a full Znuny container is heavy and not needed for the
normal test suite.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

import pymysql
import pytest
import pytest_asyncio
from _helpers import znuny_console, znuny_perl_eval  # noqa: F401 — re-exported for test modules
from peer_env import resolve as resolve_peer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

GOLDEN_DIR = Path(__file__).resolve().parent
DEFAULT_DB_URL = "mysql+pymysql://znuny:znuny@127.0.0.1:3307/znuny"


def _golden_enabled() -> bool:
    return os.environ.get("GOLDEN") == "1"


def _peer_id() -> str:
    return os.environ.get("GOLDEN_PEER", "znuny-6.5")


def _peer_is_itsm() -> bool:
    """True when the selected ``GOLDEN_PEER`` is an ITSM variant (``itsm: true`` in peers.yaml)."""
    return resolve_peer(_peer_id())["GOLDEN_ITSM"] == "1"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "golden: golden-master tests comparing real Znuny vs Tiqora (GOLDEN=1, needs Docker)",
    )
    config.addinivalue_line(
        "markers",
        "itsm_peer: needs a Znuny ITSM peer (GOLDEN_PEER=znuny-6.5-itsm | znuny-7.3-itsm); "
        "skipped on a plain peer",
    )
    config.addinivalue_line(
        "markers",
        "plain_peer: needs a plain peer without ITSM packages (e.g. GOLDEN_PEER=znuny-6.5); "
        "skipped on an ITSM peer",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if not _golden_enabled():
        skip = pytest.mark.skip(reason="set GOLDEN=1 and run `just golden-up` first")
        for item in items:
            item.add_marker(skip)
        return
    # The toolkit runs ONE peer per session (one compose project, fixed host ports), so tests
    # that need the other kind of peer are skipped, not run against the wrong one.
    itsm = _peer_is_itsm()
    for item in items:
        if item.get_closest_marker("itsm_peer") and not itsm:
            item.add_marker(
                pytest.mark.skip(reason=f"needs an ITSM peer (GOLDEN_PEER={_peer_id()} is plain)")
            )
        if item.get_closest_marker("plain_peer") and itsm:
            item.add_marker(
                pytest.mark.skip(reason=f"needs a plain peer (GOLDEN_PEER={_peer_id()} is ITSM)")
            )


@pytest.fixture(scope="session")
def golden_peer_id() -> str:
    """The peer id of this session (``GOLDEN_PEER``), e.g. ``znuny-6.5-itsm``."""
    return _peer_id()


@pytest.fixture(scope="session")
def golden_db_url() -> str:
    return os.environ.get("GOLDEN_DB_URL", DEFAULT_DB_URL)


@pytest.fixture(scope="session")
def golden_pymysql_dsn(golden_db_url: str) -> dict[str, object]:
    """Raw pymysql connection kwargs, parsed from the SQLAlchemy URL."""
    from urllib.parse import urlparse

    url = golden_db_url
    for prefix in ("mysql+pymysql://", "mysql+aiomysql://"):
        if url.startswith(prefix):
            url = "mysql://" + url[len(prefix) :]
            break
    parsed = urlparse(url)
    return {
        "host": parsed.hostname or "127.0.0.1",
        "port": parsed.port or 3307,
        "user": parsed.username or "znuny",
        "password": parsed.password or "znuny",
        "database": (parsed.path or "/znuny").lstrip("/") or "znuny",
        "charset": "utf8mb4",
        "autocommit": True,
    }


@pytest.fixture()
def golden_conn(golden_pymysql_dsn: dict[str, object]) -> Generator[pymysql.Connection, None, None]:
    """Synchronous pymysql connection for raw row inspection / diffing."""
    conn = pymysql.connect(**golden_pymysql_dsn)  # type: ignore[arg-type]
    try:
        yield conn
    finally:
        conn.close()


@pytest_asyncio.fixture(scope="session")
async def _golden_legacy_profile(golden_db_url: str) -> None:
    """Detect + apply schema profile once per golden session (groups table, …).

    Production app/worker call ``ensure_legacy_schema_supported`` at startup.
    Golden tests talk to the peer DB directly and must do the same so
    otrs-znuny-6.0 rebinds ``permission_groups`` → ``groups``.
    """
    from tiqora.db.legacy.profile import (
        ensure_legacy_schema_supported,
        reset_legacy_schema_profile,
    )

    async_url = golden_db_url.replace("mysql+pymysql://", "mysql+aiomysql://")
    engine = create_async_engine(async_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        override = os.environ.get("GOLDEN_SCHEMA_PROFILE", "").strip()
        async with factory() as session:
            profile = await ensure_legacy_schema_supported(
                session,
                allow_unknown=True,
                # ITSM peers (``znuny-6.5-itsm``) are no schema profile of their own: use the
                # profile of the base peer (``peers.yaml``: schema_profile).
                profile_override=override or resolve_peer(_peer_id())["GOLDEN_SCHEMA_PROFILE"],
                dialect_hint="mysql",
            )
        print(
            f"[golden] legacy schema profile={profile.profile_id.value} "
            f"groups_table={profile.groups_table} source={profile.source}"
        )
    finally:
        await engine.dispose()

    yield
    reset_legacy_schema_profile()


@pytest_asyncio.fixture()
async def golden_session_factory(
    golden_db_url: str,
    _golden_legacy_profile: None,
) -> Generator[async_sessionmaker[AsyncSession], None, None]:
    """Async SQLAlchemy session factory pointed at the shared golden DB.

    Reuses the exact same ``tiqora.znuny.*`` write paths tests exercise
    against the DB Znuny also writes to, so a Tiqora-issued write and a
    Znuny-issued write in the same test land in the same tables.
    """
    async_url = golden_db_url.replace("mysql+pymysql://", "mysql+aiomysql://")
    engine = create_async_engine(async_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()
