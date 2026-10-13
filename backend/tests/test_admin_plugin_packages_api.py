"""Package upload, inspection and delete endpoints against a real database (both dialects).

Band 62140-62159: plugin ids ``org.example.plg6214x``, repository names ``plg62140 …``. The
fixture plugin ``org.example.tables`` (short id ``tables``) is also cleaned up.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._plugins_state_helpers import to_async_url
from tests.plugins.package_builder import build_tqplugin, build_wheel, make_keypair
from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db
from tiqora.api.v1.admin.deps import get_admin_user
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import (
    TiqoraPlugin,
    TiqoraPluginData,
    TiqoraPluginPackage,
    TiqoraPluginRepository,
)
from tiqora.domain.auth import AuthenticatedUser
from tiqora.jobs.service import utc_now
from tiqora.permissions.engine import PermissionEngine
from tiqora.plugins.inspect import InstalledPlugin, PackageReport, installed_state, short_id_owners
from tiqora.plugins.repositories import build_key_ring, with_repository_signer
from tiqora.plugins.signing import TrustLevel

pytestmark = pytest.mark.db

BASE = "/api/v1/admin/plugins/packages"
TABLES = "org.example.tables"
OWN_IDS = (TABLES, "org.example.plg62140")
ROOT = AuthenticatedUser(
    id=1, login="root@localhost", first_name="Root", last_name="Admin", auth_method="session"
)
KEY_USER = AuthenticatedUser(
    id=1, login="root@localhost", first_name="Root", last_name="Admin", auth_method="api_key"
)
AGENT = AuthenticatedUser(
    id=7, login="agent@example.org", first_name="Ann", last_name="Agent", auth_method="session"
)


async def _clean(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as s:
        await s.execute(
            delete(TiqoraPluginPackage).where(TiqoraPluginPackage.plugin_id.in_(OWN_IDS))
        )
        await s.execute(delete(TiqoraPlugin).where(TiqoraPlugin.id.in_(OWN_IDS)))
        await s.execute(delete(TiqoraPluginData).where(TiqoraPluginData.short_id == "tables"))
        await s.execute(
            delete(TiqoraPluginRepository).where(TiqoraPluginRepository.name.like("plg62140%"))
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


def _app(
    factory: async_sessionmaker[AsyncSession],
    *,
    user: AuthenticatedUser | None = ROOT,
    **settings: Any,
) -> Any:
    async def _db() -> Any:
        async with factory() as session:
            yield session

    app = create_app(Settings(environment="test", **settings))
    app.state.start_restart_watch = False
    app.dependency_overrides[get_db] = _db
    if user is not None:
        app.dependency_overrides[get_admin_user] = lambda: user
    else:
        app.dependency_overrides[get_current_user] = lambda: AGENT
    return app


def _client(app: Any) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _package(tmp: Path, *, sign_with: Any = None, **wheel_kw: Any) -> bytes:
    wheel = build_wheel(tmp, "tq_fixture_tables", plugin_id=TABLES, **wheel_kw)
    return build_tqplugin(tmp, wheel, sign_with=sign_with).read_bytes()


def _files(data: bytes) -> dict[str, Any]:
    return {"file": ("a.tqplugin", data, "application/zip")}


async def _count(factory: async_sessionmaker[AsyncSession]) -> int:
    async with factory() as s:
        return int(
            (
                await s.execute(
                    select(func.count())
                    .select_from(TiqoraPluginPackage)
                    .where(TiqoraPluginPackage.plugin_id.in_(OWN_IDS))
                )
            ).scalar_one()
        )


async def test_upload_returns_report_and_stores(
    factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    data = _package(tmp_path)
    async with _client(_app(factory, plugins_dir=str(tmp_path))) as client:
        r = await client.post(BASE, files=_files(data))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["plugin_id"] == TABLES and body["installable"] is False
    assert body["sha256"] == hashlib.sha256(data).hexdigest()
    assert body["source"] == "upload" and body["action"] == "install"
    assert any("TIQORA_PLUGINS_ALLOW_UNSIGNED" in p for p in body["problems"])
    assert {"kind": "table", "value": "tqp_tables_note"} in body["capabilities"]
    async with factory() as s:
        row = (await s.execute(select(TiqoraPluginPackage))).scalars().first()
        stored = (
            await s.execute(
                select(TiqoraPluginPackage).where(TiqoraPluginPackage.sha256 == body["sha256"])
            )
        ).scalar_one()
    assert row is not None and stored.uploaded_by == 1 and stored.size_bytes == len(data)
    assert PackageReport.model_validate_json(stored.report).plugin_id == TABLES


async def test_upload_same_file_twice_200(
    factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    data = _package(tmp_path)
    async with _client(_app(factory, plugins_dir=str(tmp_path))) as client:
        first = await client.post(BASE, files=_files(data))
        again = await client.post(BASE, files=_files(data))
    assert (first.status_code, again.status_code) == (201, 200)
    assert first.json()["sha256"] == again.json()["sha256"]
    assert await _count(factory) == 1


async def test_upload_too_large_413(factory: async_sessionmaker[AsyncSession]) -> None:
    async with _client(_app(factory, plugins_max_package_mb=1)) as client:
        r = await client.post(BASE, files=_files(b"\0" * (1024 * 1024 + 512 * 1024)))
    assert r.status_code == 413
    assert r.json()["detail"] == "Das Paket ist größer als 1 MB"
    assert await _count(factory) == 0


async def test_upload_garbage_422(factory: async_sessionmaker[AsyncSession]) -> None:
    async with _client(_app(factory)) as client:
        r = await client.post(BASE, files=_files(b"PK\x03\x04garbage"))
    assert r.status_code == 422
    assert r.json()["detail"] and "PK" not in r.json()["detail"]  # no package bytes echoed
    assert await _count(factory) == 0


async def test_upload_signed_with_trusted_key_installable(
    factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    private, pub, kid = make_keypair()
    data = _package(tmp_path, sign_with=private)
    app = _app(factory, plugins_dir=str(tmp_path), plugins_trusted_keys=pub)
    async with _client(app) as client:
        r = await client.post(BASE, files=_files(data))
    body = r.json()
    assert r.status_code == 201, r.text
    assert body["trust"] == "local" and body["key_id"] == kid
    assert body["problems"] == [] and body["installable"] is True
    assert {"kind": "table", "value": "tqp_tables_note"} in body["capabilities"]


async def test_get_reevaluates_trust(
    factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    private, pub, _kid = make_keypair()
    data = _package(tmp_path, sign_with=private)
    async with _client(_app(factory, plugins_dir=str(tmp_path))) as client:
        up = await client.post(BASE, files=_files(data))
    assert up.json()["trust"] == "unsigned" and up.json()["installable"] is False
    sha = up.json()["sha256"]
    async with _client(_app(factory, plugins_dir=str(tmp_path), plugins_trusted_keys=pub)) as c2:
        got = await c2.get(f"{BASE}/{sha}")
    assert got.status_code == 200, got.text
    assert got.json()["trust"] == "local" and got.json()["installable"] is True
    assert got.json()["uploaded_at"] == up.json()["uploaded_at"]
    async with _client(_app(factory, plugins_dir=str(tmp_path))) as c3:
        assert (await c3.get(f"{BASE}/{'0' * 64}")).status_code == 404


async def test_delete_package(factory: async_sessionmaker[AsyncSession], tmp_path: Path) -> None:
    data = _package(tmp_path)
    async with _client(_app(factory, plugins_dir=str(tmp_path))) as client:
        sha = (await client.post(BASE, files=_files(data))).json()["sha256"]
        async with factory() as s:
            s.add(
                TiqoraPlugin(
                    id=TABLES,
                    name="t",
                    version="1.0.0",
                    enabled=False,
                    source="upload",
                    installed_at=utc_now(),
                    updated_at=utc_now(),
                    package_sha256=sha,
                )
            )
            await s.commit()
        blocked = await client.delete(f"{BASE}/{sha}")
        assert blocked.status_code == 409 and "deinstallieren" in blocked.json()["detail"]
        assert await _count(factory) == 1
        async with factory() as s:
            await s.execute(delete(TiqoraPlugin).where(TiqoraPlugin.id == TABLES))
            await s.commit()
        assert (await client.delete(f"{BASE}/{sha}")).status_code == 204
        assert (await client.delete(f"{BASE}/{sha}")).status_code == 404
    assert await _count(factory) == 0


async def test_requires_admin(
    factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def not_admin(self: PermissionEngine, user_id: int) -> bool:
        return False

    monkeypatch.setattr(PermissionEngine, "is_admin", not_admin)
    async with _client(_app(factory, user=None)) as client:
        assert (await client.post(BASE, files=_files(_package(tmp_path)))).status_code == 403
        assert (await client.get(f"{BASE}/{'0' * 64}")).status_code == 403
        assert (await client.delete(f"{BASE}/{'0' * 64}")).status_code == 403
    assert await _count(factory) == 0


async def test_cross_site_upload_refused(
    factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    settings = Settings(environment="test")
    app = _app(factory, csrf_origin_check_enabled=True)
    async with _client(app) as client:
        client.cookies.set(settings.session_cookie_name, "x")
        r = await client.post(
            BASE, files=_files(_package(tmp_path)), headers={"origin": "https://evil.example"}
        )
    assert r.status_code == 403
    assert await _count(factory) == 0


async def test_api_key_upload_refused_even_for_admin(
    factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    """F13: package upload needs a browser session; an admin API key gets 403."""
    data = _package(tmp_path)
    async with _client(_app(factory, user=KEY_USER, plugins_dir=str(tmp_path))) as client:
        r = await client.post(BASE, files=_files(data))
        assert r.status_code == 403 and r.json()["detail"] == "Nur in der Weboberfläche möglich"
        assert (await client.get(f"{BASE}/{'0' * 64}")).status_code == 404  # reads stay open
    assert await _count(factory) == 0


async def test_upload_while_runtime_install_unusable_still_inspects(
    factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    private, pub, _kid = make_keypair()
    data = _package(tmp_path, sign_with=private)
    missing = tmp_path / "no-such-dir"
    async with _client(_app(factory, plugins_dir=str(missing), plugins_trusted_keys=pub)) as client:
        r = await client.post(BASE, files=_files(data))
        sha = r.json()["sha256"]
        got = await client.get(f"{BASE}/{sha}")
    body = r.json()
    assert r.status_code == 201 and body["plugin_id"] == TABLES
    assert body["trust"] == "local" and body["installable"] is False
    assert any("existiert nicht" in p for p in body["problems"])
    assert any("existiert nicht" in p for p in got.json()["problems"])


async def test_short_id_owned_by_other_plugin_refused_same_id_adopts(
    factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    """F2: kept data of another plugin id blocks the short id; the same id adopts it."""
    private, pub, _kid = make_keypair()
    data = _package(tmp_path, sign_with=private)
    async with factory() as s:
        s.add(
            TiqoraPluginData(short_id="tables", plugin_id="org.example.plg62140", kept_at=utc_now())
        )
        await s.commit()
    app = _app(factory, plugins_dir=str(tmp_path), plugins_trusted_keys=pub)
    async with _client(app) as client:
        refused = await client.post(BASE, files=_files(data))
        assert refused.status_code == 201 and refused.json()["installable"] is False
        assert any(
            "Kurzname „tables“ wird schon von org.example.plg62140 verwendet" in p
            for p in refused.json()["problems"]
        )
        async with factory() as s:
            await s.execute(delete(TiqoraPluginData).where(TiqoraPluginData.short_id == "tables"))
            s.add(TiqoraPluginData(short_id="tables", plugin_id=TABLES, kept_at=utc_now()))
            await s.commit()
        adopted = await client.get(f"{BASE}/{refused.json()['sha256']}")
    assert adopted.json()["problems"] == [] and adopted.json()["installable"] is True


async def test_update_is_recognised_from_installed_row(
    factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    private, pub, _kid = make_keypair()
    old = _package(tmp_path, sign_with=private)
    new = _package(tmp_path, sign_with=private, version="1.1.0")
    app = _app(factory, plugins_dir=str(tmp_path), plugins_trusted_keys=pub)
    async with _client(app) as client:
        old_sha = (await client.post(BASE, files=_files(old))).json()["sha256"]
        async with factory() as s:
            s.add(
                TiqoraPlugin(
                    id=TABLES,
                    name="t",
                    version="1.0.0",
                    enabled=True,
                    source="upload",
                    installed_at=utc_now(),
                    updated_at=utc_now(),
                    package_sha256=old_sha,
                    short_id="tables",
                )
            )
            await s.commit()
        r = await client.post(BASE, files=_files(new))
        same = await client.get(f"{BASE}/{old_sha}")
    assert r.json()["action"] == "update" and r.json()["installed_version"] == "1.0.0"
    assert r.json()["installable"] is True
    assert same.json()["action"] == "reinstall"


async def test_installed_state_marks_rows_without_files_not_as_image(
    factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    """F1: a runtime row stays a runtime plugin (not an image id) even before its files exist."""
    async with factory() as s:
        s.add(
            TiqoraPlugin(
                id=TABLES,
                name="t",
                version="1.0.0",
                enabled=True,
                source="upload",
                installed_at=utc_now(),
                updated_at=utc_now(),
                package_sha256="a" * 64,
                short_id="tables",
            )
        )
        await s.commit()
    async with factory() as s:
        installed, images = await installed_state(s, Settings(plugins_dir=str(tmp_path)))
        owners = await short_id_owners(s)
    assert TABLES not in images
    assert installed[TABLES] == InstalledPlugin(TABLES, "1.0.0", "upload", "a" * 64, ())
    assert owners["tables"] == TABLES


async def test_key_ring_uses_only_verified_enabled_repositories(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    private, pub, kid = make_keypair()
    _p2, pub2, kid2 = make_keypair()

    def index(key_id: str, public_key: str) -> str:
        return json.dumps(
            {
                "index_version": 1,
                "repository": {
                    "id": "org.example.plugins",
                    "name": "Example",
                    "generated_at": "2026-10-12T08:00:00Z",
                },
                "publishers": [
                    {
                        "id": "org.example",
                        "name": "Example GmbH",
                        "keys": [{"key_id": key_id, "public_key": public_key, "revoked": False}],
                    }
                ],
                "plugins": [],
            }
        )

    now = utc_now()

    def repo(name: str, url: str, text: str, *, verified: bool, enabled: bool = True) -> Any:
        return TiqoraPluginRepository(
            name=name,
            url=url,
            url_key=hashlib.sha256(url.encode()).hexdigest(),
            enabled=enabled,
            origin="admin",
            index_json=text,
            index_verified=verified,
            created_at=now,
            updated_at=now,
        )

    async with factory() as s:
        s.add(
            repo("plg62140 ok", "https://a.example.org/index.json", index(kid, pub), verified=True)
        )
        s.add(
            repo(
                "plg62140 unverified",
                "https://b.example.org/index.json",
                index(kid2, pub2),
                verified=False,
            )
        )
        await s.commit()
    async with factory() as s:
        ring = await build_key_ring(s, Settings())
    assert [p.key_id for p in ring.publishers if p.publisher_id == "org.example"] == [kid]
    assert ring.local == {} and ring.official == {}
    del private


def test_with_repository_signer_names_the_repository() -> None:
    from tiqora.plugins.signing import KeyRing, PublisherKey, parse_public_key

    _private, pub, kid = make_keypair()
    ring = KeyRing(
        official={},
        local={},
        publishers=[
            PublisherKey(kid, parse_public_key(pub), "org.example", "Example GmbH", 7, False)
        ],
    )
    base = dict(
        sha256="a" * 64,
        size_bytes=1,
        plugin_id=TABLES,
        short_id="tables",
        name="t",
        version="1.0.0",
        publisher="Example GmbH",
        publisher_id="org.example",
        description="",
        license=None,
        homepage=None,
        support=None,
        categories=[],
        changelog=None,
        key_id=kid,
        capabilities=[],
        problems=[],
        warnings=[],
        installed_version=None,
        action="install",
        installable=True,
    )
    verified = PackageReport(**base, trust=TrustLevel.VERIFIED_PUBLISHER, signer="Example GmbH")  # type: ignore[arg-type]
    out = with_repository_signer(verified, ring, {7: "Example Erweiterungen"})
    assert out.signer == "Example GmbH (über Paketquelle Example Erweiterungen)"
    local = PackageReport(**base, trust=TrustLevel.LOCAL, signer="Eigener Schlüssel")  # type: ignore[arg-type]
    assert with_repository_signer(local, ring, {7: "x"}) is local
