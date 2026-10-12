"""Shared helpers of the ITSM mode tests (detection, mode, admin API).

Every helper works on a database URL (sync driver) so the tests run on both dialects and, for
Mode A, on the ITSM databases of ``_itsm_db``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import create_engine, delete, insert, select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tests._itsm_db import ITSM_FIXTURES, to_async_url
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraSettings
from tiqora.extensions import Registry, set_registry_for_tests
from tiqora.itsm.providers import CI_PROVIDERS, CiProviderFactory
from tiqora.itsm.znuny_adapter.detect import clear_detect_cache

PLAIN_URL_FIXTURES = ["mariadb_znuny_url", "postgres_znuny_url"]


def clean_rows(url: str) -> None:
    """Remove everything the mode tests write (settings rows, package rows)."""
    clear_detect_cache()
    engine = create_engine(url)
    try:
        TiqoraBase.metadata.create_all(engine)
        with engine.begin() as conn:
            conn.execute(delete(TiqoraSettings).where(TiqoraSettings.key.like("itsm.%")))
            if "package_repository" in _tables(conn):
                conn.execute(text("DELETE FROM package_repository"))
    finally:
        engine.dispose()


def _tables(conn: Any) -> set[str]:
    from sqlalchemy import inspect

    return set(inspect(conn).get_table_names())


def prepare(url: str, request: pytest.FixtureRequest) -> str:
    """Clean now and after the test; returns ``url``."""
    clean_rows(url)
    request.addfinalizer(lambda: clean_rows(url))
    return url


def itsm_url(request: pytest.FixtureRequest, dialect: str, profile: str) -> str:
    return prepare(request.getfixturevalue("itsm_db_url")(dialect, profile), request)


def plain_url(request: pytest.FixtureRequest, url_fixture: str) -> str:
    return prepare(request.getfixturevalue(url_fixture), request)


def profile_packages(profile: str) -> dict[str, str]:
    data = json.loads((ITSM_FIXTURES / profile / "packages.json").read_text())
    return {p["name"]: p["version"] for p in data["packages"]}


def seed_packages(url: str, packages: dict[str, str]) -> None:
    clear_detect_cache()
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM package_repository"))
            for name, version in packages.items():
                conn.execute(
                    text(
                        "INSERT INTO package_repository (name, version, vendor, install_status,"
                        " content, create_time, create_by, change_time, change_by)"
                        " VALUES (:n, :v, 'Znuny GmbH', 'installed', '',"
                        " '2000-01-01 00:00:00', 1, '2000-01-01 00:00:00', 1)"
                    ),
                    {"n": name, "v": version},
                )
    finally:
        engine.dispose()


def store(url: str, key: str, value: str | None) -> None:
    """Write (or with ``None`` delete) one ``tiqora_settings`` row directly."""
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(delete(TiqoraSettings).where(TiqoraSettings.key == key))
            if value is not None:
                conn.execute(insert(TiqoraSettings).values(key=key, value=value))
    finally:
        engine.dispose()


def stored(url: str, key: str) -> str | None:
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return conn.execute(
                select(TiqoraSettings.value).where(TiqoraSettings.key == key)
            ).scalar_one_or_none()
    finally:
        engine.dispose()


def make_factory(url: str) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(to_async_url(url))
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class FakeCmdbProvider:
    """Stands in for the Mode B provider: only ``is_empty`` is used by the mode rules."""

    def __init__(self, state: SimpleNamespace) -> None:
        self._state = state

    async def is_empty(self, session: AsyncSession) -> bool:
        return bool(self._state.empty)


def register_fake_mode_b(request: pytest.FixtureRequest, *, empty: bool) -> SimpleNamespace:
    """Registry with a fake Mode B factory; ``state.empty`` can be flipped by the test."""
    state = SimpleNamespace(empty=empty)
    registry = Registry()
    create: Callable[[Any], Any] = lambda _sysconfig: FakeCmdbProvider(state)  # noqa: E731
    registry.add(CI_PROVIDERS, CiProviderFactory(mode="tiqora", create=create))
    registry.freeze()
    set_registry_for_tests(registry)
    request.addfinalizer(lambda: set_registry_for_tests(None))
    return state
