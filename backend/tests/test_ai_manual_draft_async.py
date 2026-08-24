"""DB + HTTP tests for the async Manual Assist draft flow (Task:
Manual-Assist-Draft asynchron machen — nginx-90s-timeout fix).

``POST /tickets/{id}/ai/draft`` used to run the agent synchronously in the
request; Hetzner-hosted reasoning models can take 4-7 minutes, well past
nginx's ``proxy_read_timeout 90s``. The route now does the synchronous
pre-flight + lock check, flips ``manual_run_status`` to ``"running"`` and
returns immediately, then runs the agent in a background
``asyncio.create_task``. These tests monkeypatch ``asyncio.create_task`` to
capture (rather than schedule) that coroutine so the background run can be
awaited inline, deterministically, instead of racing a real background task.

Seed ids use the 96xx range via ``_seed_ticket`` (shared helper, see
``test_ai_runtime.py``) with ``ns`` in 70-73 — the highest ``ns`` used
elsewhere in the suite is 100 for isolated files, but 60/64/80/91/100 are
already taken; 70-73 is free.

Not on ``tests/db_leak_baseline.txt`` — every test cleans up everything it
commits (``_cleanup_ticket``, FK-safe order), so it also passes under
``TIQORA_STRICT_DB_LEAKS=1`` run on its own.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.test_ai_runtime import (
    NOW,
    ScriptedLlm,
    _mysql_async,
    _propose_response,
    _seed_ticket,
    _setup_policy,
)
from tiqora.ai.llm import LlmMessage, LlmResponse, LlmTimeoutError
from tiqora.ai.models import AUTONOMY_FULL
from tiqora.domain.settings_store import KEY_OPERATION_MODE

pytestmark = pytest.mark.db


def _to_async_url(sync_url: str) -> str:
    return _mysql_async(sync_url)


def _group_id(seed: dict[str, Any]) -> int:
    """``_seed_ticket`` doesn't return ``group_id`` — it's ``9630 + ns``,
    deterministic from ``agent_id`` (``9600 + ns``), see its docstring."""
    return int(seed["agent_id"]) + 30


def _cleanup_ticket(
    sync_url: str, *, ticket_id: int, queue_id: int, agent_id: int, group_id: int
) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        for stmt, params in (
            (
                "DELETE FROM article_data_mime WHERE article_id IN "
                "(SELECT id FROM article WHERE ticket_id = :tid)",
                {"tid": ticket_id},
            ),
            (
                "DELETE FROM tiqora_ai_article_origin WHERE article_id IN "
                "(SELECT id FROM article WHERE ticket_id = :tid)",
                {"tid": ticket_id},
            ),
            ("DELETE FROM ticket_history WHERE ticket_id = :tid", {"tid": ticket_id}),
            ("DELETE FROM article WHERE ticket_id = :tid", {"tid": ticket_id}),
            ("DELETE FROM tiqora_ai_audit_log WHERE ticket_id = :tid", {"tid": ticket_id}),
            ("DELETE FROM tiqora_ai_usage WHERE ticket_id = :tid", {"tid": ticket_id}),
            ("DELETE FROM tiqora_cache_invalidation WHERE ticket_id = :tid", {"tid": ticket_id}),
            ("DELETE FROM tiqora_event_outbox WHERE ticket_id = :tid", {"tid": ticket_id}),
            ("DELETE FROM tiqora_ai_ticket_state WHERE ticket_id = :tid", {"tid": ticket_id}),
            ("DELETE FROM tiqora_ai_draft WHERE ticket_id = :tid", {"tid": ticket_id}),
            ("DELETE FROM ticket WHERE id = :tid", {"tid": ticket_id}),
            ("DELETE FROM tiqora_ai_queue_policy WHERE queue_id = :qid", {"qid": queue_id}),
            ("DELETE FROM tiqora_ai_acl WHERE subject_id = :uid", {"uid": agent_id}),
            (
                "DELETE FROM group_user WHERE user_id = :uid OR group_id = :gid",
                {"uid": agent_id, "gid": group_id},
            ),
            ("DELETE FROM queue WHERE id = :qid", {"qid": queue_id}),
            ("DELETE FROM permission_groups WHERE id = :gid", {"gid": group_id}),
            ("DELETE FROM users WHERE id = :uid", {"uid": agent_id}),
            ("DELETE FROM tiqora_settings WHERE `key` = :k", {"k": KEY_OPERATION_MODE}),
        ):
            conn.execute(text(stmt), params)
    engine.dispose()


class _FakeTask:
    """Stand-in for the ``asyncio.Task`` returned by ``asyncio.create_task``
    — real scheduling is replaced by capturing the coroutine so the test can
    await it inline instead of racing a real background task."""

    def add_done_callback(self, cb: Any) -> None:
        pass


def _capture_background_task(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace ``asyncio.create_task`` as seen by ``tiqora.api.v1.ai`` only —
    rebinding the module-global ``asyncio`` name there to a proxy, rather
    than mutating the real ``asyncio`` module (which would also break every
    other ``create_task`` call in the app, e.g. SQLAlchemy's own internal
    usage during session teardown)."""
    import asyncio as real_asyncio

    import tiqora.api.v1.ai as ai_module

    captured: dict[str, Any] = {}

    class _AsyncioProxy:
        def __getattr__(self, name: str) -> Any:
            return getattr(real_asyncio, name)

        def create_task(self, coro: Any, *a: Any, **kw: Any) -> _FakeTask:
            captured["coro"] = coro
            return _FakeTask()

    monkeypatch.setattr(ai_module, "asyncio", _AsyncioProxy())
    return captured


class _RaisingLlm:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def chat(
        self,
        *,
        messages: list[LlmMessage],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = None,
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> LlmResponse:
        raise self._exc


async def _client_for(
    sync_url: str, *, user_id: int, login: str, monkeypatch: pytest.MonkeyPatch
) -> tuple[AsyncClient, Any]:
    from tiqora.api.app import create_app
    from tiqora.api.deps import get_current_user, get_db
    from tiqora.config import Settings
    from tiqora.domain.auth import AuthenticatedUser

    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _override_get_db() -> Any:
        async with factory() as session:
            yield session

    # The background task (tiqora.api.v1.ai._run_manual_draft_background)
    # opens its OWN session via tiqora.db.engine.get_session_factory() — by
    # design, it must not depend on the request-scoped session, which is
    # already closed by the time it runs in production. That call bypasses
    # FastAPI's dependency-override mechanism entirely, so it must be
    # monkeypatched separately to point at the same test engine, or it falls
    # back to production Settings (a real Postgres/MariaDB the test
    # environment doesn't have running).
    import tiqora.api.v1.ai as ai_module

    monkeypatch.setattr(ai_module, "get_session_factory", lambda *a, **kw: factory)

    fake_user = AuthenticatedUser(
        id=user_id,
        login=login,
        first_name="Manual",
        last_name="Assist",
        auth_method="session",
        email=f"{login}@example.com",
    )

    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = lambda: fake_user
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    return client, engine


async def _setup(
    mariadb_znuny_url: str, *, ns: int, monkeypatch: pytest.MonkeyPatch
) -> tuple[dict[str, Any], AsyncClient, Any, str]:
    seed = _seed_ticket(mariadb_znuny_url, ns=ns)
    async_engine = create_async_engine(_to_async_url(mariadb_znuny_url))
    factory = async_sessionmaker(async_engine, expire_on_commit=False)
    async with factory() as session:
        await _setup_policy(session, seed=seed, autonomy=AUTONOMY_FULL, enabled_manual_assist=True)
    await async_engine.dispose()
    login = f"agent.airuntime.96{ns}"
    client, client_engine = await _client_for(
        mariadb_znuny_url, user_id=seed["agent_id"], login=login, monkeypatch=monkeypatch
    )
    return seed, client, client_engine, login


async def test_post_draft_returns_started_and_marks_running(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed, client, client_engine, _login = await _setup(
        mariadb_znuny_url, ns=70, monkeypatch=monkeypatch
    )
    captured = _capture_background_task(monkeypatch)

    import tiqora.api.v1.ai as ai_module

    async def _fake_build_llm_client(*a: Any, **kw: Any) -> ScriptedLlm:
        return ScriptedLlm([_propose_response("reply", "Async draft body.")])

    async def _fake_kb_bundle(*a: Any, **kw: Any) -> None:
        return None

    monkeypatch.setattr(ai_module, "build_llm_client", _fake_build_llm_client)
    monkeypatch.setattr(ai_module, "kb_bundle", _fake_kb_bundle)

    try:
        resp = await client.post(f"/api/v1/tickets/{seed['ticket_id']}/ai/draft")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "started"
        assert body["draft_id"] is None
        assert body["article_id"] is None
        assert body["notes"] is None

        state_resp = await client.get(f"/api/v1/tickets/{seed['ticket_id']}/ai")
        assert state_resp.status_code == 200
        state_body = state_resp.json()
        assert state_body["manual_run_status"] == "running"
        assert state_body["manual_run_started_at"] is not None
        assert state_body["manual_run_error_code"] is None

        # A second request while the first run is still "running" must be
        # rejected deterministically (423) rather than starting a second run.
        second = await client.post(f"/api/v1/tickets/{seed['ticket_id']}/ai/draft")
        assert second.status_code == 423
        assert "ai_run_locked" in second.json()["detail"]

        # Now let the captured background run actually finish.
        assert "coro" in captured
        await captured["coro"]

        final_state = await client.get(f"/api/v1/tickets/{seed['ticket_id']}/ai")
        final_body = final_state.json()
        assert final_body["manual_run_status"] == "drafted"
        assert final_body["manual_run_error_code"] is None
        assert len(final_body["drafts"]) == 1
        assert final_body["drafts"][0]["body"] == "Async draft body."
    finally:
        await client.aclose()
        await client_engine.dispose()
        _cleanup_ticket(
            mariadb_znuny_url,
            ticket_id=seed["ticket_id"],
            queue_id=seed["queue_id"],
            agent_id=seed["agent_id"],
            group_id=_group_id(seed),
        )


async def test_background_run_llm_timeout_sets_error_status(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed, client, client_engine, _login = await _setup(
        mariadb_znuny_url, ns=71, monkeypatch=monkeypatch
    )
    captured = _capture_background_task(monkeypatch)

    import tiqora.api.v1.ai as ai_module

    async def _fake_build_llm_client(*a: Any, **kw: Any) -> _RaisingLlm:
        return _RaisingLlm(LlmTimeoutError("provider timed out"))

    async def _fake_kb_bundle(*a: Any, **kw: Any) -> None:
        return None

    monkeypatch.setattr(ai_module, "build_llm_client", _fake_build_llm_client)
    monkeypatch.setattr(ai_module, "kb_bundle", _fake_kb_bundle)

    try:
        resp = await client.post(f"/api/v1/tickets/{seed['ticket_id']}/ai/draft")
        assert resp.status_code == 200
        assert resp.json()["status"] == "started"

        await captured["coro"]

        state_resp = await client.get(f"/api/v1/tickets/{seed['ticket_id']}/ai")
        body = state_resp.json()
        assert body["manual_run_status"] == "error"
        assert body["manual_run_error_code"] == "llm_timeout"
        assert body["manual_run_notes"] is not None
        assert body["drafts"] == []
    finally:
        await client.aclose()
        await client_engine.dispose()
        _cleanup_ticket(
            mariadb_znuny_url,
            ticket_id=seed["ticket_id"],
            queue_id=seed["queue_id"],
            agent_id=seed["agent_id"],
            group_id=_group_id(seed),
        )


async def test_get_state_stale_running_guard_reports_error_without_writing(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed, client, client_engine, _login = await _setup(
        mariadb_znuny_url, ns=72, monkeypatch=monkeypatch
    )
    try:
        stale_started_at = NOW - timedelta(minutes=20)
        async_engine = create_async_engine(_to_async_url(mariadb_znuny_url))
        async with async_engine.connect() as conn:
            await conn.execute(
                text(
                    "INSERT INTO tiqora_ai_ticket_state (ticket_id, manual_run_status,"
                    " manual_run_started_at) VALUES (:tid, 'running', :started)"
                ),
                {"tid": seed["ticket_id"], "started": stale_started_at},
            )
            await conn.commit()
        await async_engine.dispose()

        state_resp = await client.get(f"/api/v1/tickets/{seed['ticket_id']}/ai")
        assert state_resp.status_code == 200
        body = state_resp.json()
        assert body["manual_run_status"] == "error"
        assert body["manual_run_error_code"] == "internal_error"

        # Never written back to the DB — the background task might still
        # land its own outcome later.
        async_engine = create_async_engine(_to_async_url(mariadb_znuny_url))
        async with async_engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT manual_run_status FROM tiqora_ai_ticket_state"
                        " WHERE ticket_id = :tid"
                    ),
                    {"tid": seed["ticket_id"]},
                )
            ).first()
        await async_engine.dispose()
        assert row is not None
        assert row[0] == "running"
    finally:
        await client.aclose()
        await client_engine.dispose()
        _cleanup_ticket(
            mariadb_znuny_url,
            ticket_id=seed["ticket_id"],
            queue_id=seed["queue_id"],
            agent_id=seed["agent_id"],
            group_id=_group_id(seed),
        )


async def test_lock_owner_fresh_also_blocks_manual_draft(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The lock check covers BOTH stale-run-lock guards — an actively held
    ``run_lock_owner`` (e.g. an in-flight auto-reply run) must also 423 a
    manual draft POST, independent of ``manual_run_status``."""
    seed, client, client_engine, _login = await _setup(
        mariadb_znuny_url, ns=73, monkeypatch=monkeypatch
    )
    try:
        async_engine = create_async_engine(_to_async_url(mariadb_znuny_url))
        async with async_engine.connect() as conn:
            await conn.execute(
                text(
                    "INSERT INTO tiqora_ai_ticket_state (ticket_id, run_lock_owner, run_lock_at)"
                    " VALUES (:tid, 'auto:other-run', :at)"
                ),
                {"tid": seed["ticket_id"], "at": datetime.now(UTC).replace(tzinfo=None)},
            )
            await conn.commit()
        await async_engine.dispose()

        resp = await client.post(f"/api/v1/tickets/{seed['ticket_id']}/ai/draft")
        assert resp.status_code == 423
        assert "ai_run_locked" in resp.json()["detail"]
    finally:
        await client.aclose()
        await client_engine.dispose()
        _cleanup_ticket(
            mariadb_znuny_url,
            ticket_id=seed["ticket_id"],
            queue_id=seed["queue_id"],
            agent_id=seed["agent_id"],
            group_id=_group_id(seed),
        )
