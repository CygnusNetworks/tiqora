"""Widget loader, frame document and assets (C9) against a real DB (band 48760-48779)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._fake_redis import FakeRedis
from tests._row_cleanup import delete_above_marks, snapshot_marks
from tests._webchat_fixtures import (
    ZNUNY_MARK_TABLES,
    cleanup_band,
    enable_webchat,
    insert_config,
    mark_cleanup_first,
    seed_queue_agent,
)
from tiqora.api.app import create_app
from tiqora.api.deps import get_redis
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.webchat.config_cache import clear_config_cache

pytestmark = pytest.mark.db

GROUP, CONFIG, AGENT, QUEUE = 48760, 48761, 48762, 48763
CONFIG_IDS = range(48760, 48780)
KEY = "wc_" + "b" * 32
ORIGIN = "https://www.example.org"
EVIL = "https://evil.example"
SELF = "http://testserver"
FRAME = "/chat/v1/frame"
_URLS = ["mariadb_znuny_url", "postgres_znuny_url"]
parametrize_db = pytest.mark.parametrize("env", _URLS, indirect=True)


def _to_async_url(sync_url: str) -> str:
    for old, new in (
        ("postgresql+psycopg2://", "postgresql+asyncpg://"),
        ("postgresql://", "postgresql+asyncpg://"),
        ("mysql+pymysql://", "mysql+aiomysql://"),
        ("mysql://", "mysql+aiomysql://"),
    ):
        if sync_url.startswith(old):
            return sync_url.replace(old, new, 1)
    return sync_url


def _cleanup(url: str) -> None:
    engine = create_engine(url)
    with engine.begin() as conn:
        cleanup_band(
            conn, config_ids=CONFIG_IDS, queue_ids=[QUEUE], group_ids=[GROUP], user_ids=[AGENT]
        )
    engine.dispose()


@dataclass
class Env:
    url: str
    dist: Path
    redis: FakeRedis
    factory: async_sessionmaker[AsyncSession]

    def sql(self, statement: str, **params: Any) -> None:
        engine = create_engine(self.url)
        try:
            with engine.begin() as conn:
                conn.execute(text(statement), params)
        finally:
            engine.dispose()

    def channel_off(self) -> None:
        quoted = '"key"' if self.url.startswith("postgres") else "`key`"
        self.sql(f"DELETE FROM tiqora_settings WHERE {quoted} = 'channel.webchat.enabled'")


@pytest_asyncio.fixture
async def env(request: pytest.FixtureRequest, tmp_path: Path) -> AsyncIterator[Env]:
    url: str = request.getfixturevalue(request.param)
    engine = create_engine(url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
    engine.dispose()
    _cleanup(url)
    marks = snapshot_marks(url, ZNUNY_MARK_TABLES)
    engine = create_engine(url)
    with engine.begin() as conn:
        seed_queue_agent(conn, queue_id=QUEUE, group_id=GROUP, user_id=AGENT, login="wc487-st")
        insert_config(conn, config_id=CONFIG, queue_id=QUEUE, public_key=KEY, origins=[ORIGIN])
        enable_webchat(conn)
    engine.dispose()
    clear_config_cache()
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "loader.js").write_text("/* loader */")
    (dist / "frame.html").write_text("<!doctype html><title>chat</title>")
    (dist / "assets" / "app-abc123.js").write_text("console.log(1)")
    aengine = create_async_engine(_to_async_url(url))
    factory = async_sessionmaker(aengine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield Env(url, dist, FakeRedis(), factory)
    finally:
        await aengine.dispose()
        clear_config_cache()
        delete_above_marks(url, marks, first=mark_cleanup_first(marks))
        _cleanup(url)


def _client(env: Env, dist: Path | None = None, **settings: Any) -> AsyncClient:
    app = create_app(
        Settings(environment="test", webchat_dist_dir=str(dist or env.dist), **settings)
    )
    app.state.session_factory = env.factory
    app.dependency_overrides[get_redis] = lambda: env.redis
    return AsyncClient(transport=ASGITransport(app=app), base_url=SELF)


def _csp(res: Any) -> str:
    return str(res.headers["content-security-policy"])


@pytest.mark.asyncio
@parametrize_db
async def test_loader_served_with_cache_and_corp(env: Env) -> None:
    async with _client(env) as client:
        res = await client.get("/chat/v1/loader.js")
    assert res.status_code == 200 and res.text == "/* loader */"
    assert res.headers["content-type"].startswith("text/javascript")
    assert res.headers["cache-control"] == "public, max-age=300"
    assert res.headers["cross-origin-resource-policy"] == "cross-origin"
    assert "content-security-policy-report-only" not in res.headers
    assert res.headers["content-security-policy"].endswith("frame-ancestors 'none'")


@pytest.mark.asyncio
@parametrize_db
async def test_loader_404_when_channel_off(env: Env) -> None:
    env.channel_off()
    clear_config_cache()
    async with _client(env) as client:
        res = await client.get("/chat/v1/loader.js")
    assert res.status_code == 404
    assert "max-age" not in res.headers.get("cache-control", "")


@pytest.mark.asyncio
@parametrize_db
async def test_frame_csp_lists_only_allowed_origins(env: Env) -> None:
    async with _client(env) as client:
        res = await client.get(FRAME, params={"k": KEY, "origin": ORIGIN})
    csp = _csp(res)
    assert res.status_code == 200 and "<title>chat</title>" in res.text
    assert csp.endswith("frame-ancestors 'self' https://www.example.org")
    assert "script-src 'self'" in csp and "'unsafe-inline'" not in csp
    assert f"connect-src {SELF}/api/chat/v1/;" in csp
    assert res.headers["cache-control"] == "no-store"
    assert res.headers["x-robots-tag"] == "noindex"
    assert res.headers["referrer-policy"] == "same-origin"
    assert res.headers["permissions-policy"] == "camera=(), microphone=(), geolocation=()"
    assert "content-security-policy-report-only" not in res.headers


@pytest.mark.asyncio
@parametrize_db
async def test_frame_accepts_matching_referer(env: Env) -> None:
    async with _client(env) as client:
        res = await client.get(
            FRAME,
            params={"k": KEY, "origin": ORIGIN},
            headers={"Referer": f"{ORIGIN}/kontakt?x=1"},
        )
    assert res.status_code == 200


@pytest.mark.asyncio
@parametrize_db
async def test_frame_refused_for_foreign_origin(env: Env) -> None:
    async with _client(env) as client:
        res = await client.get(FRAME, params={"k": KEY, "origin": EVIL})
        assert res.status_code == 403
        assert _csp(res).endswith("frame-ancestors 'none'")
        assert EVIL not in _csp(res) and "<title>" not in res.text
        res2 = await client.get(
            FRAME,
            params={"k": KEY, "origin": ORIGIN},
            headers={"Referer": f"{EVIL}/page"},
        )
        assert res2.status_code == 403 and _csp(res2).endswith("frame-ancestors 'none'")
        for bad in ("", "*", "javascript:alert(1)", "https://www.example.org/x"):
            res3 = await client.get(FRAME, params={"k": KEY, "origin": bad})
            assert res3.status_code == 403, bad
        res4 = await client.get(FRAME, params={"k": KEY})
        assert res4.status_code == 403


@pytest.mark.asyncio
@parametrize_db
async def test_frame_preview_only_from_own_origin(env: Env) -> None:
    async with _client(env) as client:
        ok = await client.get(
            FRAME,
            params={"k": KEY, "origin": SELF, "preview": "1"},
            headers={"Referer": f"{SELF}/admin/webchat/1"},
        )
        assert ok.status_code == 200
        assert _csp(ok).endswith("frame-ancestors 'self'")
        assert ORIGIN not in _csp(ok)
        foreign = await client.get(
            FRAME,
            params={"k": KEY, "origin": SELF, "preview": "1"},
            headers={"Referer": ORIGIN},
        )
        assert foreign.status_code == 403
        assert _csp(foreign).endswith("frame-ancestors 'none'")
        no_referer = await client.get(FRAME, params={"k": KEY, "origin": SELF, "preview": "1"})
        assert no_referer.status_code == 403
        wrong_origin = await client.get(
            FRAME,
            params={"k": KEY, "origin": ORIGIN, "preview": "1"},
            headers={"Referer": f"{SELF}/admin"},
        )
        assert wrong_origin.status_code == 403
        # preview=1 does not open the normal branch for the own origin either
        plain = await client.get(FRAME, params={"k": KEY, "origin": SELF})
        assert plain.status_code == 403


@pytest.mark.asyncio
@parametrize_db
async def test_frame_preview_works_for_disabled_config_and_channel_off(env: Env) -> None:
    """R-PREVIEW-DRAFT: admins configure before switching on."""
    params = {"k": KEY, "origin": SELF, "preview": "1"}
    headers = {"Referer": f"{SELF}/admin/webchat/1"}
    async with _client(env) as client:
        env.sql("UPDATE tiqora_chat_config SET valid = :v WHERE id = :i", v=False, i=CONFIG)
        env.channel_off()
        clear_config_cache()
        res = await client.get(FRAME, params=params, headers=headers)
        assert res.status_code == 200
        assert _csp(res).endswith("frame-ancestors 'self'")
        assert ORIGIN not in _csp(res)
        # the normal branch stays closed, and so do the preview checks
        assert (await client.get(FRAME, params={"k": KEY, "origin": ORIGIN})).status_code == 404
        no_ref = await client.get(FRAME, params=params)
        assert no_ref.status_code == 403
        unknown = await client.get(FRAME, params={**params, "k": "wc_" + "0" * 32}, headers=headers)
        assert unknown.status_code == 404


@pytest.mark.asyncio
@parametrize_db
async def test_unknown_key_or_channel_off_404(env: Env) -> None:
    async with _client(env) as client:
        unknown = await client.get(FRAME, params={"k": "wc_" + "0" * 32, "origin": ORIGIN})
        assert unknown.status_code == 404
        assert _csp(unknown).endswith("frame-ancestors 'none'")
        missing = await client.get(FRAME, params={"origin": ORIGIN})
        assert missing.status_code == 404

        env.sql("UPDATE tiqora_chat_config SET valid = :v WHERE id = :i", v=False, i=CONFIG)
        clear_config_cache()
        invalid = await client.get(FRAME, params={"k": KEY, "origin": ORIGIN})
        assert invalid.status_code == 404
        assert _csp(invalid).endswith("frame-ancestors 'none'")
        env.sql("UPDATE tiqora_chat_config SET valid = :v WHERE id = :i", v=True, i=CONFIG)

        clear_config_cache()
        ok = await client.get(FRAME, params={"k": KEY, "origin": ORIGIN})
        assert ok.status_code == 200

        env.channel_off()
        clear_config_cache()
        off = await client.get(FRAME, params={"k": KEY, "origin": ORIGIN})
        assert off.status_code == 404
        assert _csp(off).endswith("frame-ancestors 'none'")
        assert ORIGIN not in _csp(off)


@pytest.mark.asyncio
@parametrize_db
async def test_assets_immutable_and_missing_build_404(env: Env, tmp_path: Path) -> None:
    async with _client(env) as client:
        res = await client.get("/chat/v1/assets/app-abc123.js")
        assert res.status_code == 200
        assert res.headers["cache-control"] == "public, max-age=31536000, immutable"
        assert (await client.get("/chat/v1/assets/nope.js")).status_code == 404
        assert (await client.get("/chat/v1/assets/..%2floader.js")).status_code == 404
    empty = tmp_path / "empty"
    empty.mkdir()
    async with _client(env, dist=empty) as client:  # app start must not crash
        assert (await client.get("/chat/v1/loader.js")).status_code == 404
        assert (await client.get(FRAME, params={"k": KEY, "origin": ORIGIN})).status_code == 404
        assert (await client.get("/chat/v1/assets/app-abc123.js")).status_code == 404
    gone = tmp_path / "does-not-exist"
    async with _client(env, dist=gone) as client:
        assert (await client.get("/chat/v1/loader.js")).status_code == 404


@pytest.mark.asyncio
@parametrize_db
async def test_spa_does_not_shadow_chat_paths(env: Env, tmp_path: Path) -> None:
    spa = tmp_path / "spa"
    spa.mkdir()
    (spa / "index.html").write_text("<html>SPA</html>")
    async with _client(env, frontend_dist_dir=str(spa), serve_frontend=True) as client:
        root = await client.get("/some/page")
        assert root.status_code == 200 and "SPA" in root.text
        res = await client.get("/chat/v1/unknown")
        assert res.status_code == 404 and "SPA" not in res.text
        assert res.headers["content-type"].startswith("application/json")
        assert (await client.get("/chat/v1/loader.js")).status_code == 200
        assert (await client.get("/chat/v1/assets/app-abc123.js")).status_code == 200


@pytest.mark.asyncio
@parametrize_db
async def test_frame_backstop_before_db(env: Env) -> None:
    async with _client(env) as client:
        for _ in range(300):
            res = await client.get(FRAME, params={"k": KEY, "origin": ORIGIN})
            assert res.status_code == 200
        limited = await client.get(FRAME, params={"k": KEY, "origin": ORIGIN})
    assert limited.status_code == 429 and "retry-after" in limited.headers


@pytest.mark.asyncio
@parametrize_db
async def test_report_only_csp_stays_on_other_paths(env: Env) -> None:
    async with _client(env) as client:
        res = await client.get("/health")
    assert "content-security-policy-report-only" in res.headers


@pytest.mark.asyncio
@parametrize_db
async def test_head_returns_headers_without_body(env: Env) -> None:
    async with _client(env) as client:
        frame = await client.head(FRAME, params={"k": KEY, "origin": ORIGIN})
        loader = await client.head("/chat/v1/loader.js")
    assert frame.status_code == 200 and frame.content == b""
    assert _csp(frame).endswith("frame-ancestors 'self' https://www.example.org")
    assert "x-frame-options" not in frame.headers
    assert frame.headers["cache-control"] == "no-store"
    assert loader.status_code == 200 and loader.content == b""
    assert loader.headers["cache-control"] == "public, max-age=300"


@pytest.mark.asyncio
@parametrize_db
async def test_non_frame_answers_under_chat_are_never_frameable(env: Env) -> None:
    async with _client(env) as client:
        unknown = await client.get("/chat/v1/unknown")
        too_long = await client.get(FRAME, params={"k": KEY, "origin": ORIGIN, "preview": "10"})
        asset = await client.get("/chat/v1/assets/app-abc123.js")
    for res in (unknown, too_long, asset):
        assert _csp(res).endswith("frame-ancestors 'none'")
    assert "content-security-policy-report-only" not in unknown.headers


@pytest.mark.asyncio
@parametrize_db
async def test_public_base_url_wins_over_forged_host(env: Env) -> None:
    base = "https://help.example.com"
    async with _client(env, public_base_url=base) as client:
        res = await client.get(
            FRAME, params={"k": KEY, "origin": ORIGIN}, headers={"Host": "evil.example"}
        )
        assert res.status_code == 200
        assert f"connect-src {base}/api/chat/v1/;" in _csp(res)
        assert "evil.example" not in _csp(res)
        ok = await client.get(
            FRAME,
            params={"k": KEY, "origin": base, "preview": "1"},
            headers={"Host": "evil.example", "Referer": f"{base}/admin/webchat/1"},
        )
        assert ok.status_code == 200 and _csp(ok).endswith("frame-ancestors 'self'")
        forged = await client.get(
            FRAME,
            params={"k": KEY, "origin": "http://evil.example", "preview": "1"},
            headers={"Host": "evil.example", "Referer": "http://evil.example/x"},
        )
        assert forged.status_code == 403


class _NoDbSession:
    """Opening is free (dependency wiring); any real use fails the test."""

    async def __aenter__(self) -> _NoDbSession:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"DB touched before the backstop: {name}")


@pytest.mark.asyncio
@parametrize_db
async def test_backstop_runs_before_any_db_access(env: Env) -> None:
    app = create_app(Settings(environment="test", webchat_dist_dir=str(env.dist)))
    app.state.session_factory = _NoDbSession
    app.dependency_overrides[get_redis] = lambda: env.redis
    async with AsyncClient(transport=ASGITransport(app=app), base_url=SELF) as client:
        warm = await client.get(FRAME, params={"k": KEY, "origin": "javascript:x"})
        assert warm.status_code == 403  # pure refusal, no DB needed
        keys = [k for k in env.redis._data if ":public:" in k]
        assert keys
        for k in keys:
            env.redis._data[k] = "300"
        res = await client.get(FRAME, params={"k": KEY, "origin": ORIGIN})
        assert res.status_code == 429
        env.redis.fail = True
        down = await client.get(FRAME, params={"k": KEY, "origin": ORIGIN})
        assert down.status_code == 503
