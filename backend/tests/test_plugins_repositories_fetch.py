"""Plugin repositories: fetching, refresh, rollback guard, daemon tick and catalog (both dialects).

Band 62260-62279: repository urls under ``*.plg62260.example.org``, plugin ids
``org.example.plg62260*``. HTTP goes through ``httpx.MockTransport`` injected as ``transport``;
the resolver is injected too (the pinned address of the request is the resolver's answer).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._plugins_state_helpers import to_async_url
from tests.plugins.package_builder import make_keypair
from tiqora.api.app import create_app
from tiqora.api.deps import get_db
from tiqora.api.v1.admin.deps import get_admin_user
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraPlugin, TiqoraPluginPackage, TiqoraPluginRepository
from tiqora.domain.auth import AuthenticatedUser
from tiqora.jobs.context import JobContext, JobFailed
from tiqora.jobs.service import utc_now
from tiqora.plugins import install_jobs, repositories
from tiqora.plugins.repo_index import MAX_INDEX_BYTES
from tiqora.plugins.repositories import (
    FetchError,
    catalog,
    fetch_bytes,
    package_url_problem,
    refresh_repository,
    run_repositories_tick,
    update_binding_ok,
    url_key,
)
from tiqora.plugins.signing import sign_bytes

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 13, 9, 0, 0)
HOST = "plg62260.example.org"
URL = f"https://one.{HOST}/index.json"
URL2 = f"https://two.{HOST}/index.json"
PRIVATE, PUB, KEY_ID = make_keypair()
OTHER_PRIVATE, OTHER_PUB, _ = make_keypair()
PRIVATE_IP = "10.0.0.5"
PLUGIN = "org.example.plg62260hello"
ADMIN = AuthenticatedUser(
    id=1, login="root@localhost", first_name="Root", last_name="Admin", auth_method="session"
)


def private_resolver(host: str, port: int) -> list[str]:
    return [PRIVATE_IP]


def loopback_resolver(host: str, port: int) -> list[str]:
    return ["127.0.0.1"]


def _version(version: str, **kw: Any) -> dict[str, Any]:
    return {
        "version": version,
        "url": f"packages/{PLUGIN}-{version}.tqplugin",
        "sha256": "a" * 64,
        "size": 4821,
        "sdk": ">=0.2,<0.3",
        "released_at": "2026-10-01T00:00:00Z",
        "changelog": f"Neu in {version}",
        **kw,
    }


def make_index(
    generated_at: str = "2026-10-12T08:00:00Z",
    *,
    versions: list[str] | None = None,
    name: str = "Hallo-Erweiterung",
    description: str = "Zeigt eine Begrüßung.",
    categories: list[str] | None = None,
    plugin_id: str = PLUGIN,
    publisher_id: str = "org.example",
) -> dict[str, Any]:
    return {
        "index_version": 1,
        "repository": {
            "id": "org.example.plugins",
            "name": "Example Erweiterungen",
            "generated_at": generated_at,
        },
        "publishers": [
            {
                "id": "org.example",
                "name": "Example GmbH",
                "keys": [{"key_id": KEY_ID, "public_key": PUB, "revoked": False}],
            }
        ],
        "plugins": [
            {
                "id": plugin_id,
                "short_id": "hello",
                "name": name,
                "publisher_id": publisher_id,
                "description": description,
                "categories": categories or ["tickets"],
                "versions": [_version(v) for v in (versions or ["1.1.0"])],
            }
        ],
    }


def encode(index: dict[str, Any]) -> bytes:
    return json.dumps(index).encode()


class Served:
    """A mock repository server: ``path -> (status, body)``; records every request."""

    def __init__(self, files: dict[str, tuple[int, bytes] | Callable[[], httpx.Response]]) -> None:
        self.files = files
        self.seen: list[httpx.Request] = []

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.seen.append(request)
            entry = self.files.get(request.url.path, (404, b""))
            if callable(entry):
                return entry()
            return httpx.Response(entry[0], content=entry[1])

        return httpx.MockTransport(handler)


def signed(index: dict[str, Any], *, key: Any = PRIVATE, path: str = "/index.json") -> dict:
    raw = encode(index)
    return {path: (200, raw), path + ".sig": (200, sign_bytes(key, raw))}


async def _clean(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as s:
        await s.execute(
            delete(TiqoraPluginRepository).where(TiqoraPluginRepository.url.like(f"%{HOST}%"))
        )
        await s.execute(delete(TiqoraPlugin).where(TiqoraPlugin.id.like("org.example.plg62260%")))
        await s.execute(
            delete(TiqoraPluginPackage).where(
                TiqoraPluginPackage.plugin_id.like("org.example.plg62260%")
            )
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


def _settings(**kw: Any) -> Settings:
    return Settings(environment="test", **kw)


async def _add_repo(
    factory: async_sessionmaker[AsyncSession],
    url: str = URL,
    *,
    public_key: str | None = PUB,
    enabled: bool = True,
    origin: str = "admin",
    name: str = "plg62260 one",
) -> int:
    async with factory() as s:
        row = TiqoraPluginRepository(
            name=name,
            url=url,
            url_key=url_key(url),
            public_key=public_key,
            enabled=enabled,
            origin=origin,
            index_verified=False,
            created_at=NOW,
            updated_at=NOW,
        )
        s.add(row)
        await s.commit()
        return row.id


async def _refresh(
    factory: async_sessionmaker[AsyncSession],
    repo_id: int,
    served: Served | None,
    *,
    settings: Settings | None = None,
    resolver: Callable[[str, int], list[str]] = private_resolver,
    now: datetime = NOW,
) -> repositories.RefreshResult:
    async with factory() as s:
        repo = await s.get(TiqoraPluginRepository, repo_id)
        assert repo is not None
        result = await refresh_repository(
            s,
            repo,
            settings=settings or _settings(),
            now=now,
            transport=served.transport() if served else None,
            resolver=resolver,
        )
        await s.commit()
        return result


async def _row(factory: async_sessionmaker[AsyncSession], repo_id: int) -> TiqoraPluginRepository:
    async with factory() as s:
        row = await s.get(TiqoraPluginRepository, repo_id)
        assert row is not None
        return row


# ---------------------------------------------------------------- refresh


async def test_refresh_https_signed_index(url_fixture: async_sessionmaker[AsyncSession]) -> None:
    rid = await _add_repo(url_fixture)
    raw = encode(make_index())
    served = Served({"/index.json": (200, raw), "/index.json.sig": (200, sign_bytes(PRIVATE, raw))})
    result = await _refresh(url_fixture, rid, served)
    assert result == repositories.RefreshResult(True, 1, None)
    row = await _row(url_fixture, rid)
    assert row.index_verified is True
    assert row.index_generated_at == datetime(2026, 10, 12, 8, 0, 0)
    assert row.last_error is None and row.last_ok_at == NOW and row.last_checked_at == NOW
    assert row.index_json == raw.decode()
    import hashlib

    assert row.index_sha256 == hashlib.sha256(raw).hexdigest()
    # connected to the resolved address, with the repository's name in the Host header
    assert {r.url.host for r in served.seen} == {PRIVATE_IP}
    assert {r.headers["host"] for r in served.seen} == {f"one.{HOST}"}
    assert all(r.headers["accept-encoding"] == "identity" for r in served.seen)


async def test_refresh_unsigned_index_without_key(
    url_fixture: async_sessionmaker[AsyncSession],
) -> None:
    rid = await _add_repo(url_fixture, public_key=None)
    served = Served({"/index.json": (200, encode(make_index()))})  # no .sig: 404
    result = await _refresh(url_fixture, rid, served)
    assert result.ok is True and result.plugins == 1
    row = await _row(url_fixture, rid)
    assert row.index_verified is False and row.index_json is not None and row.last_error is None
    assert [r.url.path for r in served.seen] == ["/index.json"]  # no key: no signature requested


async def test_refresh_key_configured_signature_missing_is_refused(
    url_fixture: async_sessionmaker[AsyncSession],
) -> None:
    rid = await _add_repo(url_fixture)
    served = Served({"/index.json": (200, encode(make_index()))})
    result = await _refresh(url_fixture, rid, served)
    assert result.ok is False and result.error == "Signatur der Paketquelle ungültig"
    row = await _row(url_fixture, rid)
    assert row.index_json is None and row.index_verified is False
    assert row.last_error == "Signatur der Paketquelle ungültig" and row.last_checked_at == NOW


async def test_refresh_bad_signature_keeps_old_index(
    url_fixture: async_sessionmaker[AsyncSession],
) -> None:
    rid = await _add_repo(url_fixture)
    good = Served(signed(make_index()))
    assert (await _refresh(url_fixture, rid, good)).ok is True
    before = await _row(url_fixture, rid)
    later = NOW + timedelta(hours=6)
    bad = Served(signed(make_index("2026-10-12T09:00:00Z", name="Neu"), key=OTHER_PRIVATE))
    result = await _refresh(url_fixture, rid, bad, now=later)
    assert result.ok is False and result.error == "Signatur der Paketquelle ungültig"
    after = await _row(url_fixture, rid)
    assert after.last_error == "Signatur der Paketquelle ungültig"
    assert after.last_checked_at == later and after.last_ok_at == NOW
    assert after.index_json == before.index_json and after.index_verified is True
    assert after.index_generated_at == before.index_generated_at


async def test_index_rollback_refused(url_fixture: async_sessionmaker[AsyncSession]) -> None:
    rid = await _add_repo(url_fixture)
    assert (
        await _refresh(url_fixture, rid, Served(signed(make_index("2026-10-12T08:00:00Z"))))
    ).ok is True
    stored = (await _row(url_fixture, rid)).index_json
    older = Served(signed(make_index("2026-10-01T08:00:00Z", name="Alt")))
    result = await _refresh(url_fixture, rid, older)
    assert result.ok is False
    assert result.error is not None and "älteren Stand" in result.error
    row = await _row(url_fixture, rid)
    assert row.index_json == stored
    assert row.index_generated_at == datetime(2026, 10, 12, 8, 0, 0)
    assert row.last_error is not None and "älteren Stand" in row.last_error
    # the same generated_at is fine (a re-publish of the same state)
    same = Served(signed(make_index("2026-10-12T08:00:00Z")))
    assert (await _refresh(url_fixture, rid, same)).ok is True


async def test_redirect_not_followed(url_fixture: async_sessionmaker[AsyncSession]) -> None:
    rid = await _add_repo(url_fixture, public_key=None)
    served = Served(
        {
            "/index.json": lambda: httpx.Response(
                302, headers={"location": "https://169.254.169.254/latest"}
            )
        }
    )
    result = await _refresh(url_fixture, rid, served)
    assert result.ok is False and result.error is not None and "Weiterleitungen" in result.error
    assert len(served.seen) == 1  # the location was never requested
    assert (await _row(url_fixture, rid)).last_error == result.error


async def test_loopback_blocked_private_allowed(
    url_fixture: async_sessionmaker[AsyncSession],
) -> None:
    rid = await _add_repo(url_fixture, public_key=None)
    served = Served({"/index.json": (200, encode(make_index()))})
    blocked = await _refresh(url_fixture, rid, served, resolver=loopback_resolver)
    assert blocked.ok is False and blocked.error is not None
    assert "interne oder lokale Adresse" in blocked.error
    assert served.seen == []  # blocked before any connection
    allowed = await _refresh(url_fixture, rid, served, resolver=private_resolver)
    assert allowed.ok is True and len(served.seen) == 1


async def test_oversized_index_cut_off(url_fixture: async_sessionmaker[AsyncSession]) -> None:
    rid = await _add_repo(url_fixture, public_key=None)
    chunk = b"x" * 65536
    sent = 0

    async def body() -> AsyncIterator[bytes]:
        nonlocal sent
        for _ in range(4 * MAX_INDEX_BYTES // len(chunk)):
            sent += len(chunk)
            yield chunk

    served = Served({"/index.json": lambda: httpx.Response(200, content=body())})
    result = await _refresh(url_fixture, rid, served)
    assert result.ok is False and result.error is not None and "zu groß" in result.error
    assert 0 < sent <= MAX_INDEX_BYTES + 64 * 1024
    assert (await _row(url_fixture, rid)).index_json is None


async def test_fetch_errors_are_german() -> None:
    served = Served(
        {
            "/boom": (500, b"no"),
            "/gone": (404, b""),
            "/big": lambda: httpx.Response(
                200, headers={"content-length": "9999999"}, content=b"x"
            ),
        }
    )
    kw: dict[str, Any] = {
        "max_bytes": 1000,
        "settings": _settings(),
        "transport": served.transport(),
        "resolver": private_resolver,
    }
    with pytest.raises(FetchError, match="HTTP 500"):
        await fetch_bytes(f"https://one.{HOST}/boom", **kw)
    with pytest.raises(FetchError) as gone:
        await fetch_bytes(f"https://one.{HOST}/gone", **kw)
    assert gone.value.not_found is True
    with pytest.raises(FetchError, match="zu groß"):
        await fetch_bytes(f"https://one.{HOST}/big", **kw)
    with pytest.raises(FetchError, match="nur https"):
        await fetch_bytes("http://one.example.org/index.json", **kw)
    with pytest.raises(FetchError, match="nicht erlaubt"):
        await fetch_bytes("https://u:p@one.example.org/index.json", **kw)

    def unresolvable(host: str, port: int) -> list[str]:
        raise repositories.OutboundURLError(f"cannot resolve host {host!r}: boom")

    kw["resolver"] = unresolvable
    with pytest.raises(FetchError, match="aufgelöst"):
        await fetch_bytes(f"https://one.{HOST}/boom", **kw)


async def test_file_repository_inside_root_only(
    url_fixture: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "index.json").write_bytes(encode(make_index()))
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "index.json").write_bytes(encode(make_index()))
    (root / "link.json").symlink_to(outside / "index.json")
    settings = _settings(plugin_repository_file_root=str(root))
    # the fragment only carries the band marker for cleanup; fetching uses the path
    rid = await _add_repo(url_fixture, f"file://{root}/index.json#{HOST}", public_key=None)
    ok = await _refresh(url_fixture, rid, None, settings=settings)
    assert ok.ok is True and ok.plugins == 1
    for bad in (
        f"file://{outside}/index.json",
        f"file://{root}/link.json",
        f"file://{root}/../outside/index.json",
    ):
        with pytest.raises(FetchError, match="außerhalb"):
            await fetch_bytes(bad, max_bytes=MAX_INDEX_BYTES, settings=settings)
    with pytest.raises(FetchError) as missing:
        await fetch_bytes(f"file://{root}/nope.json", max_bytes=10, settings=settings)
    assert missing.value.not_found is True
    with pytest.raises(FetchError, match="zu groß"):
        await fetch_bytes(f"file://{root}/index.json", max_bytes=10, settings=settings)


# ---------------------------------------------------------------- tick


async def test_tick_without_repositories_makes_no_request(
    url_fixture: async_sessionmaker[AsyncSession],
) -> None:
    await _add_repo(url_fixture, enabled=False)

    def refuse(request: httpx.Request) -> httpx.Response:
        pytest.fail(f"unexpected request to {request.url}")

    result = await run_repositories_tick(
        _settings(), url_fixture, transport=httpx.MockTransport(refuse), resolver=private_resolver
    )
    assert result == {"repositories": 0, "failed": 0, "updates_available": 0}


async def test_tick_purges_stale_packages_and_counts_updates(
    url_fixture: async_sessionmaker[AsyncSession],
) -> None:
    rid = await _add_repo(url_fixture)
    broken = await _add_repo(url_fixture, URL2, public_key=None, name="plg62260 two")
    now = utc_now().replace(microsecond=0)
    async with url_fixture() as s:
        s.add(
            TiqoraPlugin(
                id=PLUGIN,
                name="Hallo",
                version="1.0.0",
                enabled=True,
                source="repository",
                signature_status="verified_publisher",
                installed_at=now,
                updated_at=now,
                repository_id=rid,
            )
        )
        for sha, age, installed in (("1" * 64, 48, None), ("2" * 64, 1, None), ("3" * 64, 48, now)):
            s.add(
                TiqoraPluginPackage(
                    sha256=sha,
                    plugin_id=f"org.example.plg62260pkg{sha[0]}",
                    version="1.0.0",
                    size_bytes=3,
                    data=b"abc",
                    manifest="",
                    report="{}",
                    signature_status="unsigned",
                    source="upload",
                    uploaded_at=now - timedelta(hours=age),
                    installed_at=installed,
                )
            )
        await s.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.headers["host"]
        if host.startswith("one."):
            files = signed(make_index(versions=["1.0.0", "1.1.0"]))
            code, body = files.get(request.url.path, (404, b""))
            return httpx.Response(code, content=body)
        raise httpx.ConnectError("down")  # the second repository is unreachable

    result = await run_repositories_tick(
        _settings(), url_fixture, transport=httpx.MockTransport(handler), resolver=private_resolver
    )
    assert result == {"repositories": 2, "failed": 1, "updates_available": 1}
    good, bad = await _row(url_fixture, rid), await _row(url_fixture, broken)
    assert good.last_error is None and good.index_verified is True
    assert bad.last_error is not None and "nicht erreichbar" in bad.last_error
    async with url_fixture() as s:
        left = set(
            (
                await s.execute(
                    select(TiqoraPluginPackage.sha256).where(
                        TiqoraPluginPackage.plugin_id.like("org.example.plg62260pkg%")
                    )
                )
            ).scalars()
        )
    assert left == {"2" * 64, "3" * 64}  # only the old never-installed package is purged


async def test_tick_syncs_env_repositories(
    url_fixture: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    (tmp_path / "index.json").write_bytes(encode(make_index()))
    env_url = f"file://{tmp_path}/index.json"
    settings = _settings(
        plugin_repository_file_root=str(tmp_path),
        plugin_repositories=json.dumps([{"name": f"plg62260 env {HOST}", "url": env_url}]),
    )
    try:
        result = await run_repositories_tick(settings, url_fixture)
        assert result == {"repositories": 1, "failed": 0, "updates_available": 0}
        async with url_fixture() as s:
            row = (
                await s.execute(
                    select(TiqoraPluginRepository).where(TiqoraPluginRepository.url == env_url)
                )
            ).scalar_one()
            assert row.origin == "env" and row.index_json is not None
    finally:
        async with url_fixture() as s:
            await s.execute(
                delete(TiqoraPluginRepository).where(TiqoraPluginRepository.url == env_url)
            )
            await s.commit()


# ---------------------------------------------------------------- catalog


async def test_catalog_filters_and_flags(
    url_fixture: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    verified = await _add_repo(url_fixture)
    plain = await _add_repo(url_fixture, URL2, public_key=None, name="plg62260 two")
    other_id = "org.example.plg62260other"
    await _refresh(
        url_fixture,
        verified,
        Served(signed(make_index(versions=["1.0.0", "1.2.0"], categories=["tickets", "chat"]))),
    )
    # the unverified repository claims the publisher namespace for a plugin under another one
    foreign = make_index(
        name="Fremd",
        description="Etwas ganz anderes",
        categories=["reports"],
        plugin_id="org.evil.plg62260x",
    )
    foreign["plugins"].append(make_index(plugin_id=other_id, name="Zweite")["plugins"][0])
    await _refresh(url_fixture, plain, Served({"/index.json": (200, encode(foreign))}))
    now = utc_now().replace(microsecond=0)
    async with url_fixture() as s:
        s.add(
            TiqoraPlugin(
                id=PLUGIN,
                name="Hallo",
                version="1.0.0",
                enabled=True,
                source="repository",
                signature_status="verified_publisher",
                installed_at=now,
                updated_at=now,
                repository_id=verified,
            )
        )
        await s.commit()
    monkeypatch.setattr("tiqora.plugins.runtime.image_plugin_ids", lambda settings: {other_id})

    async def run(**kw: Any) -> repositories.CatalogOut:
        async with url_fixture() as s:
            return await catalog(s, settings=_settings(), **kw)

    everything = await run()
    mine = {(e.repository_id, e.plugin_id): e for e in everything.entries}
    assert set(mine) == {(verified, PLUGIN), (plain, "org.evil.plg62260x"), (plain, other_id)}
    assert everything.categories == ["chat", "reports", "tickets"]
    hello = mine[(verified, PLUGIN)]
    assert hello.publisher == "Example GmbH" and hello.publisher_verified is True
    assert hello.latest_version == "1.2.0" and hello.latest_compatible == "1.2.0"
    assert hello.installed_version == "1.0.0" and hello.update_available is True
    assert hello.image_plugin is False and hello.icon_url is None
    assert [v.version for v in hello.versions] == ["1.2.0", "1.0.0"]
    assert all(v.compatible and not v.yanked for v in hello.versions)
    # an unverified index never earns "verifizierter Herausgeber"; the namespace must match too
    assert mine[(plain, other_id)].publisher_verified is False
    assert mine[(plain, "org.evil.plg62260x")].publisher_verified is False
    assert mine[(plain, other_id)].image_plugin is True
    assert mine[(plain, other_id)].update_available is False  # not installed
    # filters
    by_name = await run(q="FREMD")
    assert [e.plugin_id for e in by_name.entries] == ["org.evil.plg62260x"]
    assert [e.plugin_id for e in (await run(q="Example gmbh")).entries].count(PLUGIN) == 1
    assert [e.plugin_id for e in (await run(q="ganz anderes")).entries] == ["org.evil.plg62260x"]
    assert {e.plugin_id for e in (await run(category="CHAT")).entries} == {PLUGIN}
    assert {e.repository_id for e in (await run(repository_id=plain)).entries} == {plain}
    assert (await run(q="gibt-es-nicht")).entries == []
    assert (await run(q="gibt-es-nicht")).categories == ["chat", "reports", "tickets"]
    # a disabled repository is not offered
    async with url_fixture() as s:
        repo = await s.get(TiqoraPluginRepository, plain)
        assert repo is not None
        repo.enabled = False
        await s.commit()
    assert {e.repository_id for e in (await run()).entries} == {verified}


async def test_catalog_marks_yanked_and_incompatible_versions(
    url_fixture: async_sessionmaker[AsyncSession],
) -> None:
    rid = await _add_repo(url_fixture, public_key=None)
    index = make_index(versions=["1.0.0", "2.0.0", "3.0.0"])
    index["plugins"][0]["versions"][1]["yanked"] = True
    index["plugins"][0]["versions"][2]["sdk"] = ">=9"
    await _refresh(url_fixture, rid, Served({"/index.json": (200, encode(index))}))
    async with url_fixture() as s:
        out = await catalog(s, settings=_settings())
    entry = next(e for e in out.entries if e.plugin_id == PLUGIN)
    assert entry.latest_version == "3.0.0"  # newest that is not yanked, compatible or not
    assert entry.latest_compatible == "1.0.0"
    flags = {v.version: (v.compatible, v.yanked) for v in entry.versions}
    assert flags == {"3.0.0": (False, False), "2.0.0": (True, True), "1.0.0": (True, False)}


def _installed(**kw: Any) -> TiqoraPlugin:
    base: dict[str, Any] = {
        "id": PLUGIN,
        "name": "x",
        "version": "1.0.0",
        "enabled": True,
        "source": "repository",
        "signature_status": "verified_publisher",
        "installed_at": NOW,
        "updated_at": NOW,
    }
    return TiqoraPlugin(**{**base, **kw})


def _repo(repo_id: int) -> TiqoraPluginRepository:
    return TiqoraPluginRepository(id=repo_id, name="r", url=URL, url_key="k", enabled=True)  # type: ignore[call-arg]


def test_update_binding_rules() -> None:
    own = _installed(repository_id=1)
    assert update_binding_ok(own, _repo(1), set()) is True
    assert update_binding_ok(own, _repo(2), {KEY_ID}) is False  # no key recorded on the install
    keyed = _installed(repository_id=1, key_id=KEY_ID)
    assert update_binding_ok(keyed, _repo(2), {KEY_ID}) is True  # same key, other repository
    assert update_binding_ok(keyed, _repo(2), {"0" * 16}) is False
    assert update_binding_ok(keyed, _repo(2), set()) is False  # unverified repository: no keys
    upload = _installed(source="upload", key_id=None)
    assert update_binding_ok(upload, _repo(2), {KEY_ID}) is False
    image = _installed(source="image")
    assert update_binding_ok(image, _repo(2), set()) is True  # information only


def test_package_url_problem_for_unverified_indexes() -> None:
    index = "https://one.example.org/plugins/index.json"
    own = "https://one.example.org/plugins/packages/a.tqplugin"
    assert package_url_problem(index, own, verified=False) is None
    assert (
        package_url_problem(index, "https://ONE.example.org:443/x.tqplugin", verified=False) is None
    )
    for foreign in (
        "https://other.example.org/a.tqplugin",
        "https://one.example.org:8443/a.tqplugin",
        "https://10.0.0.5/a.tqplugin",
    ):
        problem = package_url_problem(index, foreign, verified=False)
        assert problem is not None and "ohne Schlüssel" in problem
        assert package_url_problem(index, foreign, verified=True) is None


# ---------------------------------------------------------------- job and endpoints


def _client(factory: async_sessionmaker[AsyncSession], **settings: Any) -> AsyncClient:
    async def _db() -> Any:
        async with factory() as session:
            yield session

    app = create_app(Settings(environment="test", **settings))
    app.state.start_restart_watch = False
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_admin_user] = lambda: ADMIN
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_refresh_endpoint_enqueues_job(url_fixture: async_sessionmaker[AsyncSession]) -> None:
    from tiqora.db.tiqora.models import TiqoraJob

    rid = await _add_repo(url_fixture)
    base = f"/api/v1/admin/plugin-repositories/{rid}/refresh"
    try:
        async with _client(url_fixture) as c:
            first = await c.post(base)
            assert first.status_code == 202, first.text
            again = await c.post(base)
            assert again.status_code == 202
            assert again.json()["job_id"] == first.json()["job_id"]  # dedupe `repo:<id>`
            assert (
                await c.post("/api/v1/admin/plugin-repositories/999999/refresh")
            ).status_code == 404
        async with url_fixture() as s:
            job = await s.get(TiqoraJob, first.json()["job_id"])
            assert job is not None
            assert job.kind == "core.plugin_repository_refresh"
            assert job.dedupe_key == f"repo:{rid}"
            assert install_jobs.PLUGIN_REPOSITORY_REFRESH_JOB.lane == "plugins"
    finally:
        async with url_fixture() as s:
            await s.execute(delete(TiqoraJob).where(TiqoraJob.dedupe_key == f"repo:{rid}"))
            await s.commit()


async def test_catalog_endpoint_is_not_shadowed_by_id_routes(
    url_fixture: async_sessionmaker[AsyncSession],
) -> None:
    rid = await _add_repo(url_fixture, public_key=None)
    await _refresh(url_fixture, rid, Served({"/index.json": (200, encode(make_index()))}))
    async with _client(url_fixture) as c:
        r = await c.get("/api/v1/admin/plugin-repositories/catalog", params={"q": "hallo"})
        assert r.status_code == 200, r.text
        entries = [e for e in r.json()["entries"] if e["plugin_id"] == PLUGIN]
        assert len(entries) == 1 and entries[0]["repository_id"] == rid
        assert entries[0]["publisher_verified"] is False
        assert "tickets" in r.json()["categories"]
        empty = await c.get(
            "/api/v1/admin/plugin-repositories/catalog", params={"q": "nichts-passt-hier"}
        )
        assert empty.json()["entries"] == []


async def test_refresh_job_stores_index_and_reports_result(
    url_fixture: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    (tmp_path / "index.json").write_bytes(encode(make_index()))
    url = f"file://{tmp_path}/index.json#{HOST}"
    # the fragment keeps the cleanup pattern; fetch uses the path only
    rid = await _add_repo(url_fixture, url, public_key=None)
    settings = _settings(plugin_repository_file_root=str(tmp_path))
    ctx = JobContext(
        job_id=1,
        kind=install_jobs.PLUGIN_REPOSITORY_REFRESH_KIND,
        created_by=1,
        settings=settings,
        session_factory=url_fixture,
    )
    done = await install_jobs.run_plugin_repository_refresh(
        ctx, install_jobs.PluginRepositoryRefreshPayload(repository_id=rid)
    )
    assert done == {"ok": True, "plugins": 1, "error": None}
    assert (await _row(url_fixture, rid)).index_json is not None
    (tmp_path / "index.json").write_bytes(b"{not json")
    failed = await install_jobs.run_plugin_repository_refresh(
        ctx, install_jobs.PluginRepositoryRefreshPayload(repository_id=rid)
    )
    assert failed["ok"] is False and "JSON" in failed["error"]
    assert (await _row(url_fixture, rid)).index_json is not None  # last good index stays
    with pytest.raises(JobFailed, match="nicht gefunden"):
        await install_jobs.run_plugin_repository_refresh(
            ctx, install_jobs.PluginRepositoryRefreshPayload(repository_id=999999)
        )
