"""Downloading a release from a repository and update detection (both dialects).

Band 62280-62299: repository urls under ``*.plg62280.example.org``, plugin ids
``org.example.plg62280*``. HTTP goes through ``httpx.MockTransport`` injected as ``transport``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._plugins_state_helpers import to_async_url
from tests.plugins.package_builder import build_tqplugin, build_wheel, make_keypair
from tiqora.api.app import create_app
from tiqora.api.deps import get_db
from tiqora.api.v1.admin.deps import get_admin_user
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import (
    TiqoraJob,
    TiqoraPlugin,
    TiqoraPluginPackage,
    TiqoraPluginRepository,
)
from tiqora.domain.auth import AuthenticatedUser
from tiqora.jobs.context import JobContext, JobFailed
from tiqora.plugins import install_jobs, packages_store
from tiqora.plugins.inspect import PackageReport
from tiqora.plugins.repositories import (
    FetchError,
    available_updates,
    download_package,
    refresh_repository,
    url_key,
)
from tiqora.plugins.signing import sign_bytes

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 13, 9, 0, 0)
HOST = "plg62280.example.org"
URL_A = f"https://a.{HOST}/index.json"
URL_B = f"https://b.{HOST}/index.json"
PLUGIN = "org.example.plg62280hello"
PRIVATE, PUB, KEY_ID = make_keypair()
OTHER_PRIVATE, OTHER_PUB, OTHER_KEY_ID = make_keypair()
ADMIN = AuthenticatedUser(
    id=1, login="root@localhost", first_name="Root", last_name="Admin", auth_method="session"
)


def private_resolver(host: str, port: int) -> list[str]:
    return ["10.0.0.5"]


def _version(version: str, data: bytes | None = None, **kw: Any) -> dict[str, Any]:
    return {
        "version": version,
        "url": f"packages/{PLUGIN}-{version}.tqplugin",
        "sha256": hashlib.sha256(data).hexdigest() if data is not None else "a" * 64,
        "size": len(data) if data is not None else 4821,
        "sdk": ">=0.2,<0.3",
        "released_at": "2026-10-01T00:00:00Z",
        **kw,
    }


def make_index(
    versions: list[dict[str, Any]],
    *,
    publisher_id: str = "org.example",
    key_id: str = KEY_ID,
    public_key: str = PUB,
    generated_at: str = "2026-10-12T08:00:00Z",
) -> dict[str, Any]:
    return {
        "index_version": 1,
        "repository": {
            "id": "org.example.plugins",
            "name": "Example",
            "generated_at": generated_at,
        },
        "publishers": [
            {
                "id": publisher_id,
                "name": "Example GmbH",
                "keys": [{"key_id": key_id, "public_key": public_key, "revoked": False}],
            }
        ],
        "plugins": [
            {
                "id": PLUGIN,
                "short_id": "hello",
                "name": "Hallo",
                "publisher_id": publisher_id,
                "versions": versions,
            }
        ],
    }


def package_bytes(
    tmp: Path, version: str, *, sign: Any = PRIVATE, plugin_id: str = PLUGIN
) -> bytes:
    wheel = build_wheel(tmp, plugin_id=plugin_id, version=version)
    return build_tqplugin(tmp, wheel, sign_with=sign).read_bytes()


class Served:
    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files
        self.seen: list[str] = []

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.seen.append(request.url.path)
            body = self.files.get(request.url.path)
            if body is None:
                return httpx.Response(404)
            return httpx.Response(200, content=body)

        return httpx.MockTransport(handler)


def signed_files(index: dict[str, Any], extra: dict[str, bytes] | None = None) -> dict[str, bytes]:
    raw = json.dumps(index).encode()
    return {"/index.json": raw, "/index.json.sig": sign_bytes(PRIVATE, raw), **(extra or {})}


async def _clean(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as s:
        await s.execute(
            delete(TiqoraPluginRepository).where(TiqoraPluginRepository.url.like(f"%{HOST}%"))
        )
        await s.execute(delete(TiqoraPlugin).where(TiqoraPlugin.id.like("org.example.plg62280%")))
        await s.execute(
            delete(TiqoraPluginPackage).where(
                TiqoraPluginPackage.plugin_id.like("org.example.plg62280%")
            )
        )
        await s.execute(
            delete(TiqoraJob).where(TiqoraJob.dedupe_key.like("fetch:%:org.example.plg62280%"))
        )
        await s.commit()


@pytest.fixture(name="factory", params=["mariadb_znuny_url", "postgres_znuny_url"])
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


def _settings(tmp_path: Path, **kw: Any) -> Settings:
    plugins = tmp_path / "plugins-dir"
    plugins.mkdir(exist_ok=True)
    return Settings(environment="test", plugins_dir=str(plugins), **kw)


async def _add_repo(
    factory: async_sessionmaker[AsyncSession],
    url: str = URL_A,
    *,
    public_key: str | None = PUB,
    name: str = "plg62280 a",
) -> int:
    async with factory() as s:
        row = TiqoraPluginRepository(
            name=name,
            url=url,
            url_key=url_key(url),
            public_key=public_key,
            enabled=True,
            origin="admin",
            index_verified=False,
            created_at=NOW,
            updated_at=NOW,
        )
        s.add(row)
        await s.commit()
        return row.id


async def _refresh(
    factory: async_sessionmaker[AsyncSession], repo_id: int, served: Served, tmp_path: Path
) -> None:
    async with factory() as s:
        repo = await s.get(TiqoraPluginRepository, repo_id)
        assert repo is not None
        result = await refresh_repository(
            s,
            repo,
            settings=_settings(tmp_path),
            now=NOW,
            transport=served.transport(),
            resolver=private_resolver,
        )
        await s.commit()
    assert result.ok, result.error


async def _packages(factory: async_sessionmaker[AsyncSession]) -> list[TiqoraPluginPackage]:
    async with factory() as s:
        rows = await s.execute(
            select(TiqoraPluginPackage).where(
                TiqoraPluginPackage.plugin_id.like("org.example.plg62280%")
            )
        )
        return list(rows.scalars())


async def _download(
    factory: async_sessionmaker[AsyncSession],
    repo_id: int,
    served: Served,
    tmp_path: Path,
    *,
    version: str = "1.1.0",
    settings: Settings | None = None,
) -> Any:
    return await download_package(
        factory,
        repository_id=repo_id,
        plugin_id=PLUGIN,
        version=version,
        settings=settings or _settings(tmp_path),
        uploaded_by=1,
        transport=served.transport(),
        resolver=private_resolver,
    )


async def _setup(
    factory: async_sessionmaker[AsyncSession], tmp_path: Path, *, size_delta: int = 0
) -> tuple[int, Served, bytes]:
    data = package_bytes(tmp_path, "1.1.0")
    entry = _version("1.1.0", data)
    entry["size"] += size_delta
    served = Served(signed_files(make_index([entry]), {f"/packages/{PLUGIN}-1.1.0.tqplugin": data}))
    rid = await _add_repo(factory)
    await _refresh(factory, rid, served, tmp_path)
    return rid, served, data


# ---------------------------------------------------------------- download


async def test_download_stores_package_with_repository_source(
    factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    rid, served, data = await _setup(factory, tmp_path)
    result = await _download(factory, rid, served, tmp_path)
    assert result.package_sha256 == hashlib.sha256(data).hexdigest()
    assert result.installable is True
    rows = await _packages(factory)
    assert len(rows) == 1
    row = rows[0]
    assert (row.source, row.repository_id, row.uploaded_by) == ("repository", rid, 1)
    assert row.sha256 == result.package_sha256 and row.version == "1.1.0"
    assert row.signature_status == "verified_publisher" and row.key_id == KEY_ID
    report = PackageReport.model_validate_json(row.report)
    assert report.trust.value == "verified_publisher"
    assert report.signer == "Example GmbH (über Paketquelle plg62280 a)"
    async with factory() as s:
        assert await packages_store.get_package_data(s, row.sha256) == data
    # the package was fetched from the repository's own host
    assert f"/packages/{PLUGIN}-1.1.0.tqplugin" in served.seen


async def test_download_hash_mismatch_refused(
    factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    rid, served, data = await _setup(factory, tmp_path)
    served.files[f"/packages/{PLUGIN}-1.1.0.tqplugin"] = data + b"x"  # same name, other bytes
    with pytest.raises(FetchError, match="Prüfsumme stimmt nicht mit der Paketquelle überein"):
        await _download(factory, rid, served, tmp_path)
    assert await _packages(factory) == []


async def test_download_size_mismatch_refused(
    factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    rid, served, _data = await _setup(factory, tmp_path, size_delta=1)  # sha ok, size wrong
    with pytest.raises(FetchError, match="Größe stimmt nicht mit der Paketquelle überein"):
        await _download(factory, rid, served, tmp_path)
    assert await _packages(factory) == []


async def test_download_refuses_oversized_index_entry_without_fetching(
    factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    data = package_bytes(tmp_path, "1.1.0")
    entry = _version("1.1.0", data, size=30 * 1024 * 1024)
    served = Served(signed_files(make_index([entry])))
    rid = await _add_repo(factory)
    await _refresh(factory, rid, served, tmp_path)
    served.seen.clear()
    with pytest.raises(FetchError, match="größer als 15 MB"):
        await _download(factory, rid, served, tmp_path)
    assert served.seen == []


async def test_download_package_must_match_the_index_entry(
    factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    other = package_bytes(tmp_path, "1.0.0")  # a valid, signed package of another version
    entry = _version("1.1.0", other)
    served = Served(
        signed_files(make_index([entry]), {f"/packages/{PLUGIN}-1.1.0.tqplugin": other})
    )
    rid = await _add_repo(factory)
    await _refresh(factory, rid, served, tmp_path)
    with pytest.raises(FetchError, match="passt nicht zum Eintrag"):
        await _download(factory, rid, served, tmp_path)
    assert await _packages(factory) == []


async def test_download_conflicting_publisher_claim_is_unsigned(
    factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    # repository A (no key for this package's signer) is verified; B lists the signer's key
    data = package_bytes(tmp_path, "1.1.0")  # signed with PRIVATE
    a_index = make_index([_version("1.1.0", data)], key_id=OTHER_KEY_ID, public_key=OTHER_PUB)
    served_a = Served(signed_files(a_index, {f"/packages/{PLUGIN}-1.1.0.tqplugin": data}))
    rid_a = await _add_repo(factory)
    await _refresh(factory, rid_a, served_a, tmp_path)
    b_index = make_index([_version("1.1.0", data)])
    raw_b = json.dumps(b_index).encode()
    served_b = Served({"/index.json": raw_b, "/index.json.sig": sign_bytes(PRIVATE, raw_b)})
    rid_b = await _add_repo(factory, URL_B, name="plg62280 b")
    await _refresh(factory, rid_b, served_b, tmp_path)
    result = await _download(factory, rid_a, served_a, tmp_path)
    # A claims org.example with OTHER_KEY_ID only: the conflicting claim leaves it unsigned
    assert result.installable is False
    row = (await _packages(factory))[0]
    assert row.signature_status == "unsigned" and row.repository_id == rid_a


async def test_download_from_the_key_holder_is_unsigned_when_another_repo_claims_the_publisher(
    factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    """F10: two verified repositories, one publisher id, different keys -> unverified in both.

    The download comes from B, which DOES list the signer's key; A claims the same publisher id
    with another key. The cross-repository check must see A although only B's keys may sign.
    """
    data = package_bytes(tmp_path, "1.1.0")  # signed with PRIVATE (KEY_ID)
    a_index = make_index([_version("1.1.0", data)], key_id=OTHER_KEY_ID, public_key=OTHER_PUB)
    served_a = Served(signed_files(a_index))
    rid_a = await _add_repo(factory)
    await _refresh(factory, rid_a, served_a, tmp_path)
    b_index = make_index([_version("1.1.0", data)])
    served_b = Served(signed_files(b_index, {f"/packages/{PLUGIN}-1.1.0.tqplugin": data}))
    rid_b = await _add_repo(factory, URL_B, name="plg62280 b")
    await _refresh(factory, rid_b, served_b, tmp_path)
    result = await _download(factory, rid_b, served_b, tmp_path)
    assert result.installable is False
    row = (await _packages(factory))[0]
    assert row.signature_status == "unsigned" and row.repository_id == rid_b
    report = PackageReport.model_validate_json(row.report)
    assert report.trust.value == "unsigned" and report.signer is None


async def _two_repos_other_publisher(
    factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> tuple[int, Served, str]:
    """A is verified but claims another publisher; B vouches for the signer of the package."""
    data = package_bytes(tmp_path, "1.1.0")  # signed with KEY_ID, manifest publisher org.example
    a_index = make_index(
        [_version("1.1.0", data)], publisher_id="org.aaa", key_id=OTHER_KEY_ID, public_key=OTHER_PUB
    )
    served_a = Served(signed_files(a_index, {f"/packages/{PLUGIN}-1.1.0.tqplugin": data}))
    rid_a = await _add_repo(factory)
    await _refresh(factory, rid_a, served_a, tmp_path)
    raw_b = json.dumps(make_index([_version("1.1.0", data)])).encode()
    served_b = Served({"/index.json": raw_b, "/index.json.sig": sign_bytes(PRIVATE, raw_b)})
    rid_b = await _add_repo(factory, URL_B, name="plg62280 b")
    await _refresh(factory, rid_b, served_b, tmp_path)
    return rid_a, served_a, hashlib.sha256(data).hexdigest()


async def test_download_uses_only_the_source_repositorys_keys(
    factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    rid_a, served_a, _sha = await _two_repos_other_publisher(factory, tmp_path)
    result = await _download(factory, rid_a, served_a, tmp_path)
    # B vouches for the signer, but the package came from A, whose keys do not cover it
    assert result.installable is False
    row = (await _packages(factory))[0]
    assert row.signature_status == "unsigned" and row.repository_id == rid_a


async def test_repository_package_is_judged_by_its_source_everywhere(
    factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    rid_a, served_a, sha = await _two_repos_other_publisher(factory, tmp_path)
    settings = _settings(tmp_path)
    await _download(factory, rid_a, served_a, tmp_path)
    # the install dialog (GET) agrees with the fetch job
    async with _client(factory, settings) as c:
        got = await c.get(f"/api/v1/admin/plugins/packages/{sha}")
    assert got.status_code == 200, got.text
    assert got.json()["installable"] is False
    assert got.json()["trust"] != "verified_publisher"
    # the install job refuses it too
    ctx = JobContext(
        job_id=1,
        kind=install_jobs.PLUGIN_INSTALL_KIND,
        created_by=1,
        settings=settings,
        session_factory=factory,
    )
    with pytest.raises(JobFailed):
        await install_jobs.run_plugin_install(
            ctx, install_jobs.PluginInstallPayload(package_sha256=sha)
        )
    # the same bytes uploaded by hand keep the full ring: B's key verifies them
    async with factory() as s:
        row = await s.get(TiqoraPluginPackage, sha)
        assert row is not None
        row.source, row.repository_id = "upload", None
        await s.commit()
    async with _client(factory, settings) as c:
        again = await c.get(f"/api/v1/admin/plugins/packages/{sha}")
    assert again.json()["installable"] is True
    assert again.json()["trust"] == "verified_publisher"


async def test_download_unverified_index_cannot_name_a_foreign_host(
    factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    data = package_bytes(tmp_path, "1.1.0", sign=None)
    entry = _version("1.1.0", data, url="https://other.example.org/x.tqplugin")
    raw = json.dumps(make_index([entry])).encode()
    served = Served({"/index.json": raw})
    rid = await _add_repo(factory, public_key=None)
    await _refresh(factory, rid, served, tmp_path)
    served.seen.clear()
    with pytest.raises(FetchError, match="ohne Schlüssel"):
        await _download(factory, rid, served, tmp_path)
    assert served.seen == []


async def test_download_unknown_version_and_repository(
    factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    rid, served, _data = await _setup(factory, tmp_path)
    with pytest.raises(FetchError, match="Version nicht in der Paketquelle") as unknown:
        await _download(factory, rid, served, tmp_path, version="9.9.9")
    assert unknown.value.not_found is True
    with pytest.raises(FetchError, match="Paketquelle nicht gefunden"):
        await _download(factory, 999999, served, tmp_path)


async def test_fetch_job_reports_result_and_failure(
    factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    data = package_bytes(tmp_path, "1.1.0", sign=None)
    repo_dir = tmp_path / "repo"
    (repo_dir / "packages").mkdir(parents=True)
    (repo_dir / "packages" / f"{PLUGIN}-1.1.0.tqplugin").write_bytes(data)
    (repo_dir / "index.json").write_bytes(
        json.dumps(make_index([_version("1.1.0", data)])).encode()
    )
    settings = _settings(
        tmp_path, plugin_repository_file_root=str(repo_dir), plugins_allow_unsigned=True
    )
    rid = await _add_repo(factory, f"file://{repo_dir}/index.json#{HOST}", public_key=None)
    async with factory() as s:
        repo = await s.get(TiqoraPluginRepository, rid)
        assert repo is not None
        result = await refresh_repository(s, repo, settings=settings, now=NOW)
        await s.commit()
    assert result.ok, result.error
    ctx = JobContext(
        job_id=1,
        kind=install_jobs.PLUGIN_FETCH_KIND,
        created_by=1,
        settings=settings,
        session_factory=factory,
    )
    done = await install_jobs.run_plugin_fetch(
        ctx,
        install_jobs.PluginFetchPayload(repository_id=rid, plugin_id=PLUGIN, version="1.1.0"),
    )
    assert done == {"package_sha256": hashlib.sha256(data).hexdigest(), "installable": True}
    assert install_jobs.PLUGIN_FETCH_JOB.lane == "plugins"
    assert install_jobs.PLUGIN_FETCH_JOB.timeout_seconds == 600
    with pytest.raises(JobFailed, match="Version nicht in der Paketquelle"):
        await install_jobs.run_plugin_fetch(
            ctx,
            install_jobs.PluginFetchPayload(repository_id=rid, plugin_id=PLUGIN, version="9.9.9"),
        )


def test_fetch_job_is_registered() -> None:
    from tiqora.extensions.core import get_registry
    from tiqora.jobs.registry import JOB_HANDLERS

    kinds = {h.kind for h in get_registry().items(JOB_HANDLERS)}
    assert "core.plugin_fetch" in kinds


# ---------------------------------------------------------------- endpoint


def _client(factory: async_sessionmaker[AsyncSession], settings: Settings) -> AsyncClient:
    async def _db() -> Any:
        async with factory() as session:
            yield session

    app = create_app(settings)
    app.state.start_restart_watch = False
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_admin_user] = lambda: ADMIN
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_download_endpoint_enqueues_job_and_validates(
    factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    rid, _served, _data = await _setup(factory, tmp_path)
    base = f"/api/v1/admin/plugin-repositories/{rid}/download"
    body = {"plugin_id": PLUGIN, "version": "1.1.0"}
    async with _client(factory, _settings(tmp_path)) as c:
        first = await c.post(base, json=body)
        assert first.status_code == 202, first.text
        again = await c.post(base, json=body)
        assert (
            again.json()["job_id"] == first.json()["job_id"]
        )  # dedupe fetch:<repo>:<plugin>:<version>
        unknown = await c.post(base, json={**body, "version": "9.9.9"})
        assert unknown.status_code == 404
        assert unknown.json()["detail"] == "Version nicht in der Paketquelle"
        missing = await c.post("/api/v1/admin/plugin-repositories/999999/download", json=body)
        assert missing.status_code == 404
    async with factory() as s:
        job = await s.get(TiqoraJob, first.json()["job_id"])
        assert job is not None
        assert job.kind == "core.plugin_fetch"
        assert job.dedupe_key == f"fetch:{rid}:{PLUGIN}:1.1.0"
        assert json.loads(job.payload) == {
            "repository_id": rid,
            "plugin_id": PLUGIN,
            "version": "1.1.0",
        }


async def test_download_refused_when_runtime_install_unusable(
    factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    rid, _served, _data = await _setup(factory, tmp_path)
    off = _settings(tmp_path, plugins_runtime_install=False)
    async with _client(factory, off) as c:
        r = await c.post(
            f"/api/v1/admin/plugin-repositories/{rid}/download",
            json={"plugin_id": PLUGIN, "version": "1.1.0"},
        )
    assert r.status_code == 409
    assert "Installation zur Laufzeit ist abgeschaltet" in r.json()["detail"]
    async with factory() as s:
        queued = await s.execute(select(TiqoraJob).where(TiqoraJob.kind == "core.plugin_fetch"))
        assert [j for j in queued.scalars() if (j.dedupe_key or "").endswith(PLUGIN)] == []


async def test_download_endpoint_refuses_yanked_version(
    factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    data = package_bytes(tmp_path, "1.1.0")
    served = Served(signed_files(make_index([_version("1.1.0", data, yanked=True)])))
    rid = await _add_repo(factory)
    await _refresh(factory, rid, served, tmp_path)
    async with _client(factory, _settings(tmp_path)) as c:
        r = await c.post(
            f"/api/v1/admin/plugin-repositories/{rid}/download",
            json={"plugin_id": PLUGIN, "version": "1.1.0"},
        )
    assert r.status_code == 409 and "zurückgezogen" in r.json()["detail"]


# ---------------------------------------------------------------- updates


async def _install_row(
    factory: async_sessionmaker[AsyncSession],
    *,
    publisher_id: str | None = "org.example",
    version: str = "1.0.0",
    source: str = "repository",
    repository_id: int | None = None,
    key_id: str | None = None,
) -> None:
    sha = hashlib.sha256(b"installed " + (publisher_id or "-").encode()).hexdigest()
    async with factory() as s:
        s.add(
            TiqoraPluginPackage(
                sha256=sha,
                plugin_id=PLUGIN,
                version=version,
                size_bytes=3,
                data=b"abc",
                manifest="",
                report=json.dumps({"publisher_id": publisher_id}),
                signature_status="verified_publisher",
                source="upload",
                uploaded_at=NOW,
                installed_at=NOW,
            )
        )
        s.add(
            TiqoraPlugin(
                id=PLUGIN,
                name="Hallo",
                version=version,
                enabled=True,
                source=source,
                signature_status="verified_publisher",
                installed_at=NOW,
                updated_at=NOW,
                package_sha256=None if source == "image" else sha,
                repository_id=repository_id,
                key_id=key_id,
            )
        )
        await s.commit()


async def _updates(factory: async_sessionmaker[AsyncSession], tmp_path: Path) -> Any:
    async with factory() as s:
        out = await available_updates(s, settings=_settings(tmp_path))
        await s.rollback()
    return {k: v for k, v in out.items() if k.startswith("org.example.plg62280")}


async def _publish(
    factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    url: str,
    index: dict[str, Any],
    *,
    name: str,
    verified: bool = True,
) -> int:
    rid = await _add_repo(factory, url, public_key=PUB if verified else None, name=name)
    raw = json.dumps(index).encode()
    files = {"/index.json": raw}
    if verified:
        files["/index.json.sig"] = sign_bytes(PRIVATE, raw)
    await _refresh(factory, rid, Served(files), tmp_path)
    return rid


async def test_available_updates_same_publisher_only(
    factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    repo_a = await _publish(
        factory, tmp_path, URL_A, make_index([_version("1.1.0")]), name="plg62280 a"
    )
    # repository B offers a higher version but names another publisher for the same plugin id
    evil = make_index([_version("2.0.0")], publisher_id="org.evil")
    await _publish(factory, tmp_path, URL_B, evil, name="plg62280 b")
    await _install_row(factory, repository_id=repo_a, key_id=KEY_ID)
    updates = await _updates(factory, tmp_path)
    assert set(updates) == {PLUGIN}
    info = updates[PLUGIN]
    assert (info.version, info.repository_id, info.repository_name) == (
        "1.1.0",
        repo_a,
        "plg62280 a",
    )
    assert info.image_plugin is False


async def test_available_updates_other_publisher_alone_is_ignored(
    factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    repo = await _publish(
        factory,
        tmp_path,
        URL_A,
        make_index([_version("2.0.0")], publisher_id="org.evil"),
        name="plg62280 a",
    )
    # even the plugin's own repository cannot swap the publisher of an installed plugin
    await _install_row(factory, repository_id=repo, key_id=KEY_ID)
    assert await _updates(factory, tmp_path) == {}


async def test_available_updates_need_the_binding_to_repository_or_key(
    factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    other = await _publish(
        factory, tmp_path, URL_B, make_index([_version("1.1.0")]), name="plg62280 b"
    )
    # installed from an upload, signed by another key: a lookalike repository earns no update
    await _install_row(factory, source="upload", key_id=OTHER_KEY_ID)
    assert await _updates(factory, tmp_path) == {}
    async with factory() as s:
        row = await s.get(TiqoraPlugin, PLUGIN)
        assert row is not None
        row.key_id = KEY_ID  # the key the verified repository vouches for
        await s.commit()
    updates = await _updates(factory, tmp_path)
    assert updates[PLUGIN].repository_id == other


async def test_available_updates_skip_yanked_and_incompatible(
    factory: async_sessionmaker[AsyncSession], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from packaging.version import Version

    # a source checkout has no release version: pin one so the ``tiqora`` range counts
    monkeypatch.setattr("tiqora.plugins.compat.release_core_version", lambda: Version("0.19.0"))
    versions = [
        _version("1.0.0"),
        _version("1.1.0"),
        _version("2.0.0", yanked=True),
        _version("3.0.0", sdk=">=9"),
        _version("4.0.0", tiqora=">=99"),
    ]
    repo = await _publish(factory, tmp_path, URL_A, make_index(versions), name="plg62280 a")
    await _install_row(factory, repository_id=repo, key_id=KEY_ID)
    assert (await _updates(factory, tmp_path))[PLUGIN].version == "1.1.0"
    async with factory() as s:
        row = await s.get(TiqoraPlugin, PLUGIN)
        assert row is not None
        row.version = "1.1.0"
        await s.commit()
    assert await _updates(factory, tmp_path) == {}  # nothing newer that is usable


async def test_image_plugin_update_is_info_only(
    factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    await _publish(factory, tmp_path, URL_A, make_index([_version("1.1.0")]), name="plg62280 a")
    await _install_row(factory, source="image")
    async with factory() as s:
        row = await s.get(TiqoraPlugin, PLUGIN)
        assert row is not None
        row.package_sha256 = None
        await s.commit()
    # the publisher of an image plugin comes from its discovered manifest; none discovered here,
    # so the repository binding alone decides (image: information only)
    updates = await _updates(factory, tmp_path)
    assert updates[PLUGIN].image_plugin is True and updates[PLUGIN].version == "1.1.0"


async def test_available_updates_without_repositories_or_plugins(
    factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    assert await _updates(factory, tmp_path) == {}
    await _install_row(factory, repository_id=None)
    assert await _updates(factory, tmp_path) == {}


def test_fetch_dedupe_key_separates_releases() -> None:
    assert install_jobs.fetch_dedupe_key(1, PLUGIN, "1.1.0") != install_jobs.fetch_dedupe_key(
        1, PLUGIN, "1.2.0"
    )
