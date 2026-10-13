"""Repository configuration: env parsing and sync, input validation (both dialects).

Band 62240-62259: repository names ``plg62240 …``, urls under ``*.plg62240.example.org``.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._plugins_state_helpers import to_async_url
from tests.plugins.package_builder import make_keypair
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraPluginRepository
from tiqora.jobs.service import utc_now
from tiqora.plugins.repositories import (
    RepositoryInputError,
    parse_env_repositories,
    sync_env_repositories,
    url_key,
    validate_repository_input,
)

ROOT = Path("/var/lib/tiqora/plugin-repository")
_, PUB, _ = make_keypair()
U1 = "https://one.plg62240.example.org/index.json"
U2 = "https://two.plg62240.example.org/index.json"
U3 = "https://admin.plg62240.example.org/index.json"


def test_parse_env_repositories() -> None:
    repos, problems = parse_env_repositories(
        json.dumps(
            [
                {
                    "name": "Intern",
                    "url": "https://plugins.example.org/index.json",
                    "public_key": PUB,
                },
                {"name": "Offline", "url": "file:///srv/repo/index.json"},
                {"name": "Bad", "url": "http://plugins.example.org/index.json"},
            ]
        ),
        file_root=ROOT,
    )
    assert [r.name for r in repos] == ["Intern"] and len(problems) == 2
    assert repos[0].public_key == PUB
    assert "Offline" in problems[0] and "nicht unterhalb" in problems[0]
    assert "Bad" in problems[1] and "nur https" in problems[1]


def test_parse_env_accepts_file_below_root_and_rejects_dotdot() -> None:
    repos, problems = parse_env_repositories(
        json.dumps(
            [
                {"name": "Offline", "url": f"file://{ROOT}/index.json"},
                {"name": "Escape", "url": f"file://{ROOT}/../other/index.json"},
            ]
        ),
        file_root=ROOT,
    )
    assert [r.name for r in repos] == ["Offline"] and len(problems) == 1
    assert "Escape" in problems[0]


@pytest.mark.parametrize(
    ("raw", "fragment"),
    [
        ("", None),
        ("   ", None),
        ("{not json", "kein gültiges JSON"),
        ('{"name": "x"}', "JSON-Liste"),
        ('["x"]', "kein JSON-Objekt"),
        ('[{"name": "A", "url": 5}]', "Zeichenketten"),
        (
            '[{"name": "A", "url": "https://a.example.org/i.json", "public_key": "rsa:x"}]',
            "ed25519",
        ),
    ],
)
def test_parse_env_problems(raw: str, fragment: str | None) -> None:
    repos, problems = parse_env_repositories(raw, file_root=ROOT)
    assert repos == []
    if fragment is None:
        assert problems == []
    else:
        assert len(problems) == 1 and fragment in problems[0]


def test_parse_env_duplicate_url_is_skipped() -> None:
    repos, problems = parse_env_repositories(
        json.dumps([{"name": "A", "url": U1}, {"name": "B", "url": U1}]), file_root=ROOT
    )
    assert [r.name for r in repos] == ["A"] and "mehrfach" in problems[0]


@pytest.mark.parametrize(
    ("url", "fragment"),
    [
        ("file:///srv/index.json", "nur https"),
        ("http://a.example.org/index.json", "nur https"),
        ("javascript:alert(1)", "nur https"),
        ("https://user:pw@a.example.org/index.json", "Zugangsdaten"),
        ("https://user@a.example.org/index.json", "Zugangsdaten"),
        ("https://a.example.org/index.json#frag", "Fragment"),
        ("https:///index.json", "Servernamen"),
        ("https://a.example.org/in dex.json", "Leer"),
        ("https://a.example.org:99999/index.json", "ungültig"),
        ("https://a.example.org/" + "x" * 1000, "Zeichen lang"),
        ("", "Zeichen lang"),
    ],
)
def test_validate_rejects_admin_urls(url: str, fragment: str) -> None:
    with pytest.raises(RepositoryInputError, match=fragment):
        validate_repository_input(name="Quelle", url=url, public_key=None)


def test_validate_normalises_and_checks_key() -> None:
    assert validate_repository_input(name="  Quelle ", url=f" {U1} ", public_key=f" {PUB} ") == (
        "Quelle",
        U1,
        PUB,
    )
    assert validate_repository_input(name="Q", url=U1, public_key="  ")[2] is None
    with pytest.raises(RepositoryInputError, match="ed25519"):
        validate_repository_input(name="Q", url=U1, public_key="nonsense")
    with pytest.raises(RepositoryInputError, match="Name"):
        validate_repository_input(name="   ", url=U1, public_key=None)
    with pytest.raises(RepositoryInputError, match="Name"):
        validate_repository_input(name="x" * 201, url=U1, public_key=None)


async def _clean(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as s:
        await s.execute(
            delete(TiqoraPluginRepository).where(TiqoraPluginRepository.url.like("%plg62240%"))
        )
        await s.commit()


@pytest.fixture(name="url_fixture", params=["mariadb_znuny_url", "postgres_znuny_url"])
async def factory_fixture(
    request: pytest.FixtureRequest,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(to_async_url(request.getfixturevalue(request.param)))
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: TiqoraBase.metadata.create_all(c, checkfirst=True))
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await _clean(sm)
    try:
        yield sm
    finally:
        await _clean(sm)
        await engine.dispose()


def _env(*entries: dict[str, str]) -> Settings:
    return Settings(environment="test", plugin_repositories=json.dumps(list(entries)))


async def _rows(factory: async_sessionmaker[AsyncSession]) -> dict[str, TiqoraPluginRepository]:
    async with factory() as s:
        rows = (
            (
                await s.execute(
                    select(TiqoraPluginRepository).where(
                        TiqoraPluginRepository.url.like("%plg62240%")
                    )
                )
            )
            .scalars()
            .all()
        )
    return {r.url: r for r in rows}


@pytest.mark.db
async def test_env_sync_upserts_and_disables(
    url_fixture: async_sessionmaker[AsyncSession],
) -> None:
    factory = url_fixture
    now = utc_now()
    async with factory() as s:
        s.add(
            TiqoraPluginRepository(
                name="plg62240 admin",
                url=U3,
                url_key=url_key(U3),
                enabled=True,
                origin="admin",
                created_at=now.replace(microsecond=0),
                updated_at=now.replace(microsecond=0),
            )
        )
        await s.commit()
    two = _env(
        {"name": "plg62240 one", "url": U1, "public_key": PUB}, {"name": "plg62240 two", "url": U2}
    )
    async with factory() as s:
        assert await sync_env_repositories(s, two, now) == []
        await s.commit()
    rows = await _rows(factory)
    assert rows[U1].origin == "env" and rows[U1].enabled and rows[U1].public_key == PUB
    assert rows[U2].origin == "env" and rows[U2].enabled
    assert rows[U3].origin == "admin" and rows[U3].enabled
    # second tick is idempotent; U2 leaves the env, a verified index for a changed key resets
    async with factory() as s:
        row = (
            await s.execute(select(TiqoraPluginRepository).where(TiqoraPluginRepository.url == U1))
        ).scalar_one()
        row.index_verified = True
        await s.commit()
    one = _env({"name": "plg62240 one renamed", "url": U1, "public_key": PUB})
    async with factory() as s:
        await sync_env_repositories(s, one, now)
        await s.commit()
    rows = await _rows(factory)
    assert len(rows) == 3
    assert rows[U1].name == "plg62240 one renamed" and rows[U1].index_verified is True
    assert rows[U2].enabled is False and rows[U2].origin == "env"
    assert rows[U3].enabled is True and rows[U3].origin == "admin"
    # env drops the key: the old verification no longer holds; U2 comes back
    back = _env({"name": "plg62240 one", "url": U1}, {"name": "plg62240 two", "url": U2})
    async with factory() as s:
        await sync_env_repositories(s, back, now)
        await s.commit()
    rows = await _rows(factory)
    assert rows[U1].public_key is None and rows[U1].index_verified is False
    assert rows[U2].enabled is True


@pytest.mark.db
async def test_env_sync_never_takes_over_an_admin_row(
    url_fixture: async_sessionmaker[AsyncSession],
) -> None:
    factory = url_fixture
    now = utc_now().replace(microsecond=0)
    async with factory() as s:
        s.add(
            TiqoraPluginRepository(
                name="plg62240 mine",
                url=U3,
                url_key=url_key(U3),
                enabled=False,
                origin="admin",
                created_at=now,
                updated_at=now,
            )
        )
        await s.commit()
    async with factory() as s:
        await sync_env_repositories(s, _env({"name": "plg62240 env", "url": U3}), now)
        await s.commit()
    row = (await _rows(factory))[U3]
    assert (row.origin, row.enabled, row.name) == ("admin", False, "plg62240 mine")
