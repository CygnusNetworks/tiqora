"""DB tests for the Tiqora AI subsystem admin API (Phase A, plan §Phase A).

Follows the direct-router-call pattern from ``test_admin_daemons.py`` /
``test_mail_outbound_admin.py``: local testcontainer only (never Prod), call
router functions directly against a real async session. No network/real LLM
or MCP calls anywhere — httpx uses ``MockTransport``, MCP discovery uses an
injected ``fetch_tools`` fake.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tiqora.ai import acl as ai_acl
from tiqora.ai import mcp as ai_mcp
from tiqora.ai import policies as ai_policies
from tiqora.ai import providers as ai_providers
from tiqora.ai import usage as ai_usage
from tiqora.ai.gate import OPERATION_MODE_TIQORA_PRIMARY, set_operation_mode
from tiqora.ai.models import TiqoraLlmProvider, TiqoraMcpToolPolicy
from tiqora.api.v1.admin import ai as admin_ai
from tiqora.api.v1.admin.ai_schemas import (
    AiAclCreate,
    AiAclUpdate,
    AiQueuePolicyCreate,
    AiQueuePolicyUpdate,
    LlmProviderCreate,
    LlmProviderUpdate,
    McpClientCreate,
)
from tiqora.config import get_settings
from tiqora.crypto.secret import decrypt_secret
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.auth import AuthenticatedUser

pytestmark = pytest.mark.db


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


def _ensure_tiqora_tables(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        for table in (
            "tiqora_ai_acl",
            "tiqora_ai_usage",
            "tiqora_ai_ticket_state",
            "tiqora_ai_draft",
            "tiqora_ai_article_origin",
            "tiqora_ai_queue_policy",
            "tiqora_mcp_tool_policy",
            "tiqora_mcp_client",
            "tiqora_llm_provider",
            "tiqora_settings",
        ):
            conn.execute(text(f"DELETE FROM {table}"))
    engine.dispose()


def _root_user() -> AuthenticatedUser:
    return AuthenticatedUser(
        id=1, login="root@localhost", first_name="Admin", last_name="Znuny", auth_method="session"
    )


# ---------------------------------------------------------------------------
# LLM providers
# ---------------------------------------------------------------------------


async def test_provider_crud_never_exposes_api_key(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    get_settings.cache_clear()
    settings = get_settings()
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            created = await admin_ai.create_llm_provider(
                LlmProviderCreate(
                    name="nebius",
                    kind="openai_compat",
                    base_url="https://api.studio.nebius.ai/v1",
                    default_model="meta-llama/Llama-3.3-70B",
                    api_key="sk-super-secret",
                    eu_hosted=True,
                ),
                _root_user(),
                session,
            )
            assert created.has_api_key is True
            assert "api_key" not in created.model_dump()
            assert "sk-super-secret" not in str(created.model_dump())

            # Ciphertext roundtrips.
            row = (
                await session.execute(
                    select(TiqoraLlmProvider).where(TiqoraLlmProvider.id == created.id)
                )
            ).scalar_one()
            assert row.api_key_enc != "sk-super-secret"
            assert decrypt_secret(settings.secret_key, row.api_key_enc) == "sk-super-secret"

            listed = await admin_ai.list_llm_providers(_root_user(), session)
            assert len(listed) == 1
            assert listed[0].name == "nebius"

            updated = await admin_ai.update_llm_provider(
                created.id, LlmProviderUpdate(default_model="new-model"), _root_user(), session
            )
            assert updated.default_model == "new-model"
            assert updated.has_api_key is True  # unchanged, key preserved

            await admin_ai.delete_llm_provider(created.id, _root_user(), session)
            assert await admin_ai.list_llm_providers(_root_user(), session) == []

            with pytest.raises(HTTPException) as exc_info:
                await admin_ai.update_llm_provider(
                    999_999, LlmProviderUpdate(default_model="x"), _root_user(), session
                )
            assert exc_info.value.status_code == 404
    finally:
        await engine.dispose()
        get_settings.cache_clear()


async def test_provider_test_connection_mocked_tool_calling(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    get_settings.cache_clear()
    settings = get_settings()
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            row = await ai_providers.create_provider(
                session,
                settings=settings,
                change_by=1,
                name="p1",
                kind="openai_compat",
                base_url="https://example.com/v1",
                default_model="test-model",
                api_key="secret",
                extra_json=None,
                supports_tools=True,
                supports_streaming=True,
                eu_hosted=False,
            )

            def handler(request: httpx.Request) -> httpx.Response:
                assert request.headers["authorization"] == "Bearer secret"
                return httpx.Response(
                    200,
                    json={
                        "model": "test-model",
                        "choices": [
                            {"message": {"tool_calls": [{"id": "1", "function": {"name": "ping"}}]}}
                        ],
                    },
                )

            client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            result = await ai_providers.test_provider_connection(
                row, settings=settings, client=client
            )
            await client.aclose()
            assert result.ok is True
            assert result.tool_calling_ok is True
            assert result.model == "test-model"

            def error_handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(401, text="unauthorized")

            client2 = httpx.AsyncClient(transport=httpx.MockTransport(error_handler))
            result2 = await ai_providers.test_provider_connection(
                row, settings=settings, client=client2
            )
            await client2.aclose()
            assert result2.ok is False
            assert "401" in (result2.error or "")
    finally:
        await engine.dispose()
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# MCP clients + tool policy discovery
# ---------------------------------------------------------------------------


@dataclass
class _FakeAnnotations:
    readOnlyHint: bool | None = None
    destructiveHint: bool | None = None


@dataclass
class _FakeTool:
    name: str
    description: str | None
    annotations: Any = None


async def test_mcp_client_crud_and_discovery_upserts_tool_policies(
    mariadb_znuny_url: str,
) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    get_settings.cache_clear()
    settings = get_settings()
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            created = await admin_ai.create_mcp_client_route(
                McpClientCreate(
                    name="netadmin", url="https://mcp.example.com/mcp", auth_token="tok"
                ),
                _root_user(),
                session,
            )
            assert created.has_auth_token is True

            row = await ai_mcp.get_mcp_client(session, created.id)
            assert row is not None

            async def fake_fetch_tools(url: str, token: str | None) -> list[_FakeTool]:
                assert url == "https://mcp.example.com/mcp"
                assert token == "tok"
                return [
                    _FakeTool(
                        name="diagnose_connection",
                        description="Diagnose a port",
                        annotations=_FakeAnnotations(readOnlyHint=True),
                    ),
                    _FakeTool(
                        name="reset_port",
                        description="Reset a port (mutating)",
                        annotations=_FakeAnnotations(destructiveHint=True),
                    ),
                ]

            discovery = await ai_mcp.refresh_tools(
                session, row, settings=settings, fetch_tools=fake_fetch_tools
            )
            assert set(discovery.added) == {"diagnose_connection", "reset_port"}

            policies = await ai_mcp.list_tool_policies(session, created.id)
            by_name = {p.tool_name: p for p in policies}
            assert by_name["diagnose_connection"].enabled is False  # default-disabled
            assert by_name["diagnose_connection"].mutating is False  # readOnlyHint prefill
            assert by_name["reset_port"].mutating is True  # destructiveHint prefill

            # Admin enables one tool; a second discovery run must not clobber it.
            enabled = await ai_mcp.set_tool_policy(
                session, created.id, "diagnose_connection", enabled=True
            )
            assert enabled is not None
            assert enabled.enabled is True

            async def fake_fetch_tools_2(url: str, token: str | None) -> list[_FakeTool]:
                # reset_port disappeared from the server this time.
                return [
                    _FakeTool(
                        name="diagnose_connection",
                        description="Diagnose a port (updated)",
                        annotations=_FakeAnnotations(readOnlyHint=True),
                    )
                ]

            discovery2 = await ai_mcp.refresh_tools(
                session, row, settings=settings, fetch_tools=fake_fetch_tools_2
            )
            assert discovery2.added == []
            assert "reset_port" in discovery2.removed

            policies2 = await ai_mcp.list_tool_policies(session, created.id)
            by_name2 = {p.tool_name: p for p in policies2}
            assert by_name2["diagnose_connection"].enabled is True  # preserved
            assert "reset_port" not in by_name2  # never-enabled tool dropped on removal

            await admin_ai.delete_mcp_client_route(created.id, _root_user(), session)
            remaining = (
                (
                    await session.execute(
                        select(TiqoraMcpToolPolicy).where(
                            TiqoraMcpToolPolicy.mcp_client_id == created.id
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert remaining == []  # ON DELETE CASCADE
    finally:
        await engine.dispose()
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Queue AI policy + Readiness-Gate
# ---------------------------------------------------------------------------


async def test_queue_policy_create_validation(mariadb_znuny_url: str) -> None:
    """Pydantic's ``Literal`` already rejects a bad autonomy value at the API
    boundary; this exercises the service-layer validation directly (also
    reachable from any future non-HTTP caller)."""
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            with pytest.raises(ai_policies.QueuePolicyValidationError):
                await ai_policies.create_queue_policy(
                    session, change_by=1, queue_id=1, autonomy="bogus"
                )
    finally:
        await engine.dispose()


async def test_queue_policy_gate_enforcement_409_then_ok_then_regression(
    mariadb_znuny_url: str,
) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    get_settings.cache_clear()
    settings = get_settings()
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            provider = await ai_providers.create_provider(
                session,
                settings=settings,
                change_by=1,
                name="p",
                kind="openai_compat",
                base_url="https://example.com/v1",
                default_model="m",
                api_key=None,
                extra_json=None,
                supports_tools=True,
                supports_streaming=True,
                eu_hosted=False,
            )

            # operation_mode defaults to "parallel" -> enabling auto_reply is 409.
            with pytest.raises(HTTPException) as exc_info:
                await admin_ai.create_queue_policy_route(
                    AiQueuePolicyCreate(
                        queue_id=1,
                        enabled_auto_reply=True,
                        service_user_id=42,
                        llm_provider_id=provider.id,
                    ),
                    _root_user(),
                    session,
                )
            assert exc_info.value.status_code == 409

            # Disabled policy creation is unaffected by the gate.
            created = await admin_ai.create_queue_policy_route(
                AiQueuePolicyCreate(queue_id=1), _root_user(), session
            )
            assert created.enabled_auto_reply is False

            # Switch to tiqora_primary -> enabling now succeeds.
            await set_operation_mode(session, OPERATION_MODE_TIQORA_PRIMARY)
            enabled = await admin_ai.update_queue_policy_route(
                created.id,
                AiQueuePolicyUpdate(
                    enabled_auto_reply=True, service_user_id=42, llm_provider_id=provider.id
                ),
                _root_user(),
                session,
            )
            assert enabled.enabled_auto_reply is True

            # Regression to parallel must still be allowed to *disable*.
            await set_operation_mode(session, "parallel")
            disabled = await admin_ai.update_queue_policy_route(
                created.id, AiQueuePolicyUpdate(enabled_auto_reply=False), _root_user(), session
            )
            assert disabled.enabled_auto_reply is False

            # But re-enabling while back in parallel is 409 again.
            with pytest.raises(HTTPException) as exc_info2:
                await admin_ai.update_queue_policy_route(
                    created.id,
                    AiQueuePolicyUpdate(enabled_auto_reply=True),
                    _root_user(),
                    session,
                )
            assert exc_info2.value.status_code == 409
    finally:
        await engine.dispose()
        get_settings.cache_clear()


async def test_queue_policy_manual_assist_and_summary_enable_in_parallel_operation(
    mariadb_znuny_url: str,
) -> None:
    """Plan §3.0 v1.1 relaxation (Phase E): only ``enabled_auto_reply`` is
    gated by ``operation_mode`` — ``enabled_manual_assist``/``enabled_summary``
    must be enable-able while still in ``parallel`` operation (the default,
    unchanged here)."""
    _ensure_tiqora_tables(mariadb_znuny_url)
    get_settings.cache_clear()
    settings = get_settings()
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            provider = await ai_providers.create_provider(
                session,
                settings=settings,
                change_by=1,
                name="p-parallel",
                kind="openai_compat",
                base_url="https://example.com/v1",
                default_model="m",
                api_key=None,
                extra_json=None,
                supports_tools=True,
                supports_streaming=True,
                eu_hosted=False,
            )

            created = await admin_ai.create_queue_policy_route(
                AiQueuePolicyCreate(queue_id=1, enabled_manual_assist=True, enabled_summary=True),
                _root_user(),
                session,
            )
            assert created.enabled_manual_assist is True
            assert created.enabled_summary is True

            # Still 409 for auto_reply in the same (parallel) operation_mode —
            # service_user_id/llm_provider_id are set so the gate is the only
            # thing blocking this, isolating it from the 422 validation path.
            with pytest.raises(HTTPException) as exc_info:
                await admin_ai.update_queue_policy_route(
                    created.id,
                    AiQueuePolicyUpdate(
                        enabled_auto_reply=True,
                        service_user_id=42,
                        llm_provider_id=provider.id,
                    ),
                    _root_user(),
                    session,
                )
            assert exc_info.value.status_code == 409
    finally:
        await engine.dispose()
        get_settings.cache_clear()


async def test_queue_policy_auto_reply_requires_service_user_and_provider(
    mariadb_znuny_url: str,
) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await set_operation_mode(session, OPERATION_MODE_TIQORA_PRIMARY)
            with pytest.raises(HTTPException) as exc_info:
                await admin_ai.create_queue_policy_route(
                    AiQueuePolicyCreate(queue_id=2, enabled_auto_reply=True),
                    _root_user(),
                    session,
                )
            assert exc_info.value.status_code == 422
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------


async def test_usage_record_and_list(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await ai_usage.record_usage(
                session,
                queue_id=5,
                feature="manual_assist",
                prompt_tokens=100,
                completion_tokens=50,
            )
            await ai_usage.record_usage(
                session, queue_id=5, feature="summary", prompt_tokens=20, completion_tokens=10
            )
            await ai_usage.record_usage(
                session, queue_id=9, feature="manual_assist", prompt_tokens=1, completion_tokens=1
            )

            page = await admin_ai.list_ai_usage(
                _root_user(),
                session,
                queue_id=5,
                feature=None,
                ts_from=None,
                ts_to=None,
                page=1,
                page_size=50,
            )
            assert page.total == 2
            assert page.total_prompt_tokens == 120
            assert page.total_completion_tokens == 60
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# ACL
# ---------------------------------------------------------------------------


async def test_acl_crud_and_validation(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            created = await admin_ai.create_ai_acl(
                AiAclCreate(subject_type="group", subject_id=3, feature="summary"),
                _root_user(),
                session,
            )
            assert created.allowed is True

            listed = await admin_ai.list_ai_acl(_root_user(), session)
            assert len(listed) == 1

            # Pydantic's Literal already rejects a bad subject_type at the API
            # boundary; exercise the service-layer validation directly too
            # (also reachable from any future non-HTTP caller).
            with pytest.raises(ai_acl.AiAclValidationError):
                await ai_acl.create_acl(
                    session, subject_type="bogus", subject_id=1, feature="summary"
                )

            deleted_ok = await admin_ai.delete_ai_acl(created.id, _root_user(), session)
            assert deleted_ok is None
            assert await admin_ai.list_ai_acl(_root_user(), session) == []

            with pytest.raises(HTTPException) as exc_info2:
                await admin_ai.update_ai_acl(
                    999_999, AiAclUpdate(allowed=False), _root_user(), session
                )
            assert exc_info2.value.status_code == 404
    finally:
        await engine.dispose()
